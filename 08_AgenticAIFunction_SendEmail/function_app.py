import os, json, re
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from urllib.parse import quote_plus  # needed for Bing News fallback

# Initialize client and agent(s)
client = None
init_error = None

# --- Citations / link helpers (unchanged from your working version) ---
_URL_RE = re.compile(r'https?://[^\s\]\)]+', re.IGNORECASE)
_CITATION_MARKER_RE = re.compile(r'【[^】]+】')
_BING_MARKER_RE = re.compile(r'【\d+:\d+†source】')

def _dedup_sources(items):
    """De-duplicate sources by URL (or file_id fallback) while preserving order."""
    seen = set()
    out = []
    for s in items:
        url = (s or {}).get("url", "") or ""
        key = url or f"file:{(s or {}).get('file_id','')}"
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out

def _extract_json_block(text: str):
    """
    Try to extract a JSON object from the assistant text.
    Supports ```json ... ``` fences or a bare {...} object.
    Returns (obj | None).
    """
    if not text:
        return None
    # fenced ```json ... ```
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # first top-level JSON object (best effort)
    m2 = re.search(r"(\{.*\})", text, re.DOTALL)
    if m2:
        candidate = m2.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None

def _extract_sources_from_text(md: str, user_query: str | None = None):
    """
    Clean bracketed citation markers like  and collect URLs as sources.
    If there are no URLs and this looks like a news query, add a Bing News search link.
    Returns (clean_markdown, sources_list).
    """
    if not md:
        return md, []

    # detect Bing-style markers before stripping
    has_bing_markers = bool(_BING_MARKER_RE.search(md))

    # strip markers in displayed text
    md_clean = _CITATION_MARKER_RE.sub('', md)

    # collect inline URLs
    urls = list(dict.fromkeys(_URL_RE.findall(md_clean)))  # de-dup preserve order
    sources = [{"title": "Source", "url": u, "publisher": "", "date": ""} for u in urls]

    # synthesize Bing link for newsy queries if nothing else found
    if not sources and user_query:
        q = user_query.lower()
        if has_bing_markers or any(k in q for k in ["news", "latest", "today", "breaking", "update"]):
            sources.append({
                "title": "Bing News results",
                "url": f"https://www.bing.com/news/search?q={quote_plus(user_query)}",
                "publisher": "Bing",
                "date": ""
            })

    return md_clean.strip(), sources

# -------------------- New endpoint-style client init (minimal change) --------------------
try:
    # Endpoint-style configuration
    PROJECT_ENDPOINT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
    SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID")
    RESOURCE_GROUP  = os.environ.get("AZURE_RESOURCE_GROUP")
    PROJECT_NAME    = os.environ.get("AI_FOUNDRY_PROJECT_NAME")

    # Role-based agents (+ fallback)
    AGENT_ID_DEFAULT = os.environ.get("AGENT_ID")        # optional fallback
    AGENT_ID_USER    = os.environ.get("AGENT_ID_USER")   # useragent
    AGENT_ID_ADMIN   = os.environ.get("AGENT_ID_ADMIN")  # adminagent

    if not PROJECT_ENDPOINT:
        init_error = "AI_FOUNDRY_PROJECT_ENDPOINT environment variable not set"
    elif not (SUBSCRIPTION_ID and RESOURCE_GROUP and PROJECT_NAME):
        init_error = "AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AI_FOUNDRY_PROJECT_NAME must be set"
    elif not (AGENT_ID_USER or AGENT_ID_ADMIN or AGENT_ID_DEFAULT):
        init_error = "No agent id configured. Set AGENT_ID_USER / AGENT_ID_ADMIN (or AGENT_ID as fallback)."
    else:
        credential = DefaultAzureCredential()
        client = AIProjectClient(
            credential=credential,
            endpoint=PROJECT_ENDPOINT,
            subscription_id=SUBSCRIPTION_ID,
            resource_group_name=RESOURCE_GROUP,
            project_name=PROJECT_NAME,
        )
        # We pass agent_id per call; no upfront agent fetch.

except Exception as e:
    init_error = f"Failed to initialize Azure AI Foundry client: {str(e)}"
    client = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

def pick_agent_id(role_header: str | None) -> str:
    """
    Choose the agent id based on APIM-stamped role header.
    Falls back to AGENT_ID_DEFAULT if specific role id is missing.
    """
    role = (role_header or "").lower()
    if role == "admin" and os.environ.get("AGENT_ID_ADMIN"):
        return os.environ["AGENT_ID_ADMIN"]
    if os.environ.get("AGENT_ID_USER"):
        return os.environ["AGENT_ID_USER"]
    if os.environ.get("AGENT_ID"):
        return os.environ["AGENT_ID"]
    raise RuntimeError("No agent id available for this request")

# -------------------- Threads/Messages/Runs using new sub-clients (minimal change) --------------------
def _ensure_thread(thread_id: str | None) -> str:
    if thread_id:
        return thread_id
    t = client.agents.threads.create()
    # new SDK returns an object with .id
    return getattr(t, "id", t.get("id") if isinstance(t, dict) else t)

def _add_user_message(thread_id: str, text: str):
    client.agents.messages.create(
        thread_id=thread_id,
        role="user",
        content=text
    )

def _collect_last_assistant(thread_id: str, user_query: str | None):
    """
    Return a dict: { 'answer_md': str|None, 'answer': str|None, 'sources': list }
    Prefers structured JSON {answer_md, sources[]} if the agent returns it.
    Also collects content.text.annotations (file citations / file paths / web URLs).
    Falls back to plain text with auto-mined sources & cleaned citations.
    """
    # new SDK: iterate directly, not messages.data
    messages = client.agents.messages.list(thread_id=thread_id)

    last_text = None
    ann_sources = []  # can include file refs and web URLs found in annotations

    for msg in messages:
        if getattr(msg, "role", None) == "assistant":
            chunks = []
            content_list = getattr(msg, "content", []) or []
            for c in content_list:
                if hasattr(c, "text"):
                    tv = getattr(c.text, "value", None)
                    chunks.append(tv if tv else str(c.text))

                    # parse annotations including web URLs (like your working project)
                    anns = getattr(c.text, "annotations", None)
                    if anns:
                        for an in anns:
                            # internal file citation
                            if hasattr(an, "file_citation") and an.file_citation:
                                fc = an.file_citation
                                ann_sources.append({
                                    "title": "Document citation",
                                    "url": "",
                                    "publisher": "",
                                    "date": "",
                                    "file_id": getattr(fc, "file_id", "unknown"),
                                    "quote": getattr(fc, "quote", "")
                                })
                            # internal file path
                            elif hasattr(an, "file_path") and an.file_path:
                                fp = an.file_path
                                ann_sources.append({
                                    "title": "File attachment",
                                    "url": "",
                                    "publisher": "",
                                    "date": "",
                                    "file_id": getattr(fp, "file_id", "unknown")
                                })
                            # web/external url
                            else:
                                url = getattr(an, "url", None)
                                if url:
                                    ann_sources.append({
                                        "title": "Source",
                                        "url": url,
                                        "publisher": "",
                                        "date": ""
                                    })
                                else:
                                    # sniff a URL in the annotation string (fallback)
                                    try:
                                        s = str(an)
                                    except Exception:
                                        s = ""
                                    m = re.search(r'https?://[^\s\'">,]+', s)
                                    if m:
                                        ann_sources.append({
                                            "title": "Source",
                                            "url": m.group(0),
                                            "publisher": "",
                                            "date": ""
                                        })

                elif isinstance(getattr(msg, "content", None), str):
                    chunks.append(msg.content)

            if chunks:
                last_text = "\n\n".join(chunks)
            if last_text:
                break

    if not last_text:
        return {"answer_md": None, "answer": "(no assistant message)", "sources": []}

    # Try structured JSON in the text
    obj = _extract_json_block(last_text)
    if isinstance(obj, dict) and ("answer_md" in obj or "answer" in obj):
        sources = obj.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        answer_md = obj.get("answer_md") or obj.get("answer") or ""
        if not sources:
            answer_md, mined = _extract_sources_from_text(answer_md, user_query=user_query)
            sources = _dedup_sources((ann_sources or []) + mined)
        else:
            # even if agent provided sources, add any annotation URLs we found (then de-dup)
            sources = _dedup_sources(sources + (ann_sources or []))
        return {
            "answer_md": answer_md,
            "answer": obj.get("answer") or answer_md,
            "sources": sources
        }

    # Fallback: clean text + mined sources + annotation sources (with de-dup)
    clean_md, mined_sources = _extract_sources_from_text(last_text, user_query=user_query)
    all_sources = _dedup_sources((ann_sources or []) + mined_sources)
    return {"answer_md": clean_md, "answer": clean_md, "sources": all_sources}

def _run_and_wait(thread_id: str, agent_id: str, user_query: str | None):
    # new SDK: runs live under client.agents.runs
    client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent_id
    )
    return _collect_last_assistant(thread_id, user_query=user_query)

# --------------------------------- HTTP Trigger ---------------------------------
@app.route(route="chat", methods=[func.HttpMethod.POST])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    # Check init
    if init_error:
        return func.HttpResponse(
            json.dumps({"error": "Initialization failed", "detail": init_error}),
            status_code=503,
            mimetype="application/json"
        )

    if not client:
        return func.HttpResponse(
            json.dumps({"error": "AI service unavailable", "detail": "Azure AI Foundry client not initialized"}),
            status_code=503,
            mimetype="application/json"
        )

    try:
        body = req.get_json()
        if not body:
            return func.HttpResponse(
                json.dumps({"error": "No JSON body provided"}),
                status_code=400,
                mimetype="application/json"
            )

        text = body.get("input")
        if not text:
            return func.HttpResponse(
                json.dumps({"error": "Missing input"}),
                status_code=400,
                mimetype="application/json"
            )
        
        thread_id = body.get("thread_id")

        # Read role from APIM header and pick the proper agent
        role_header = req.headers.get("x-user-role")
        agent_id = pick_agent_id(role_header)
        
        # Process the request
        thread_id = _ensure_thread(thread_id)
        _add_user_message(thread_id, text)
        result = _run_and_wait(thread_id, agent_id=agent_id, user_query=text)

        payload = {
            "answer": result.get("answer"),
            "answer_md": result.get("answer_md"),
            "sources": result.get("sources", []),
            "thread_id": thread_id,
            "agent_id": agent_id
        }
        return func.HttpResponse(
            json.dumps(payload),
            status_code=200,
            mimetype="application/json"
        )
        
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": "Agent error", "detail": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
