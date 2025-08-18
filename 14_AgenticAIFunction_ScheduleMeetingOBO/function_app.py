import os, json, re
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from urllib.parse import quote_plus  # needed for Bing News fallback

# >>> NEW (OBO / Graph)
import requests
from msal import ConfidentialClientApplication

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
    Clean bracketed citation markers and collect URLs as sources.
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
    """
    messages = client.agents.messages.list(thread_id=thread_id)

    last_text = None
    ann_sources = []

    for msg in messages:
        if getattr(msg, "role", None) == "assistant":
            chunks = []
            content_list = getattr(msg, "content", []) or []
            for c in content_list:
                if hasattr(c, "text"):
                    tv = getattr(c.text, "value", None)
                    chunks.append(tv if tv else str(c.text))

                    anns = getattr(c.text, "annotations", None)
                    if anns:
                        for an in anns:
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
                            elif hasattr(an, "file_path") and an.file_path:
                                fp = an.file_path
                                ann_sources.append({
                                    "title": "File attachment",
                                    "url": "",
                                    "publisher": "",
                                    "date": "",
                                    "file_id": getattr(fp, "file_id", "unknown")
                                })
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
            sources = _dedup_sources(sources + (ann_sources or []))
        return {
            "answer_md": answer_md,
            "answer": obj.get("answer") or answer_md,
            "sources": sources
        }

    clean_md, mined_sources = _extract_sources_from_text(last_text, user_query=user_query)
    all_sources = _dedup_sources((ann_sources or []) + mined_sources)
    return {"answer_md": clean_md, "answer": clean_md, "sources": all_sources}

def _run_and_wait(thread_id: str, agent_id: str, user_query: str | None):
    client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent_id
    )
    return _collect_last_assistant(thread_id, user_query=user_query)

# --------------------------------- HTTP Trigger: Chat ---------------------------------
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

# --------------------------------- HTTP Trigger: Send as user (OBO → Graph) ---------------------------------
# >>> NEW (OBO / Graph)
TENANT_ID        = os.environ.get("TENANT_ID")
BACKEND_APP_ID   = os.environ.get("BACKEND_CLIENT_ID")      # app id of "MyBackendAPI"
BACKEND_SECRET   = os.environ.get("BACKEND_CLIENT_SECRET")  # client secret (or switch to cert)
GRAPH_SCOPE      = ["https://graph.microsoft.com/.default"]
GRAPH_ENDPOINT   = os.environ.get("GRAPH_ENDPOINT", "https://graph.microsoft.com/v1.0")

def _obo_get_graph_token(user_assertion: str) -> str:
    if not (TENANT_ID and BACKEND_APP_ID and BACKEND_SECRET):
        raise RuntimeError("OBO not configured. Set TENANT_ID, BACKEND_CLIENT_ID, BACKEND_CLIENT_SECRET.")
    cca = ConfidentialClientApplication(
        client_id=BACKEND_APP_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=BACKEND_SECRET
    )
    result = cca.acquire_token_on_behalf_of(user_assertion=user_assertion, scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"OBO failed: {result.get('error')}: {result.get('error_description')}")
    return result["access_token"]

def _graph_send_mail_as_user(graph_token: str, subject: str, body_html: str, recipients: list[str]):
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients]
        },
        "saveToSentItems": True
    }
    r = requests.post(
        f"{GRAPH_ENDPOINT}/me/sendMail",
        headers={"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Graph sendMail failed {r.status_code}: {r.text}")

def _coerce_recipients(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        parts = re.split(r"[;,]", v)   # accept comma/semicolon separated
        return [p.strip() for p in parts if p.strip()]
    return []

@app.route(route="send-as-user", methods=[func.HttpMethod.POST])
def send_as_user(req: func.HttpRequest) -> func.HttpResponse:
    try:
        authz = req.headers.get("Authorization", "")
        if not authz.startswith("Bearer "):
            return func.HttpResponse(
                json.dumps({"error": "Missing bearer token"}),
                status_code=401,
                mimetype="application/json"
            )
        user_token = authz.split(" ", 1)[1]

        body = req.get_json()
        if not body:
            return func.HttpResponse(
                json.dumps({"error": "No JSON body provided"}),
                status_code=400,
                mimetype="application/json"
            )

        recipients = _coerce_recipients(body.get("recipients"))
        subject    = (body.get("subject") or "").strip()
        body_html  = body.get("bodyHtml") or ""

        if not recipients or not subject or not body_html:
            return func.HttpResponse(
                json.dumps({"error": "Missing required fields: recipients[], subject, bodyHtml"}),
                status_code=400,
                mimetype="application/json"
            )

        graph_token = _obo_get_graph_token(user_token)
        _graph_send_mail_as_user(graph_token, subject, body_html, recipients)

        return func.HttpResponse(
            json.dumps({"status": "sent", "recipients": recipients, "subject": subject}),
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
            json.dumps({"error": "send-as-user failed", "detail": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

# -------------------------- NEW: schedule-as-user (OBO → Graph /me/events) --------------------------
def _build_attendees(required: list[str], optional: list[str]):
    attendees = []
    for a in required or []:
        if a:
            attendees.append({"emailAddress": {"address": a}, "type": "required"})
    for a in optional or []:
        if a:
            attendees.append({"emailAddress": {"address": a}, "type": "optional"})
    return attendees

def _graph_create_event_as_user(graph_token: str, payload: dict) -> dict:
    """
    Create a Teams meeting as the logged-in user using Graph:
    - POST /me/events   (primary calendar), or
    - POST /me/calendars/{calendarId}/events (if calendarId provided and not 'Calendar')
    Returns { webLink, joinUrl, iCalUId }.
    """
    subject    = (payload.get("subject") or "").strip()
    body_html  = payload.get("body") or ""
    tz         = payload.get("timeZone") or "SE Asia Standard Time"
    start      = payload.get("start")
    end        = payload.get("end")
    location   = payload.get("location") or "Microsoft Teams"
    cal_id     = (payload.get("calendarId") or "").strip()
    req_att    = payload.get("requiredAttendees") or []
    opt_att    = payload.get("optionalAttendees") or []

    # Build attendees (accept comma/semicolon strings too)
    if isinstance(req_att, str):
        req_att = _coerce_recipients(req_att)
    if isinstance(opt_att, str):
        opt_att = _coerce_recipients(opt_att)

    attendees = _build_attendees(req_att, opt_att)

    body = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "start": {"dateTime": start, "timeZone": tz},
        "end":   {"dateTime": end,   "timeZone": tz},
        "location": {"displayName": location},
        "attendees": attendees,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        # "allowNewTimeProposals": True  # optional
    }

    # Choose endpoint based on calendarId (primary if not specified or 'Calendar')
    if cal_id and cal_id.lower() != "calendar":
        url = f"{GRAPH_ENDPOINT}/me/calendars/{cal_id}/events"
    else:
        url = f"{GRAPH_ENDPOINT}/me/events"

    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
            "Prefer": f'outlook.timezone="{tz}"'
        },
        json=body,
        timeout=30
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Graph create event failed {r.status_code}: {r.text}")

    ev = r.json()
    return {
        "webLink": ev.get("webLink"),
        "joinUrl": (ev.get("onlineMeeting") or {}).get("joinUrl"),
        "iCalUId": ev.get("iCalUId")
    }

@app.route(route="schedule-as-user", methods=[func.HttpMethod.POST])
def schedule_as_user(req: func.HttpRequest) -> func.HttpResponse:
    """
    Request body (example):
    {
      "subject": "Design Review",
      "body": "<p>Agenda...</p>",
      "timeZone": "SE Asia Standard Time",
      "start": "2025-08-20T14:00:00",
      "end":   "2025-08-20T15:00:00",
      "calendarId": "Calendar",
      "requiredAttendees": ["a@contoso.com","b@contoso.com"],
      "optionalAttendees": [],
      "location": "Microsoft Teams"
    }
    """
    try:
        authz = req.headers.get("Authorization", "")
        if not authz.startswith("Bearer "):
            return func.HttpResponse(
                json.dumps({"error": "Missing bearer token"}),
                status_code=401,
                mimetype="application/json"
            )
        user_token = authz.split(" ", 1)[1]

        body = req.get_json()
        if not body:
            return func.HttpResponse(
                json.dumps({"error": "No JSON body provided"}),
                status_code=400,
                mimetype="application/json"
            )

        subject = (body.get("subject") or "").strip()
        start   = body.get("start")
        end     = body.get("end")
        tz      = body.get("timeZone") or "SE Asia Standard Time"
        req_att = body.get("requiredAttendees")
        # Basic validation (keep minimal)
        if not subject or not start or not end:
            return func.HttpResponse(
                json.dumps({"error": "Missing required fields: subject, start, end"}),
                status_code=400,
                mimetype="application/json"
            )
        # Require at least one attendee for invitations
        coerced_required = _coerce_recipients(req_att)
        if not coerced_required:
            return func.HttpResponse(
                json.dumps({"error": "requiredAttendees must include at least one recipient"}),
                status_code=400,
                mimetype="application/json"
            )
        body["requiredAttendees"] = coerced_required
        # Optional attendees normalization
        body["optionalAttendees"] = _coerce_recipients(body.get("optionalAttendees"))

        graph_token = _obo_get_graph_token(user_token)
        result = _graph_create_event_as_user(graph_token, body)

        return func.HttpResponse(
            json.dumps({
                "ok": True,
                "subject": subject,
                "start": start,
                "end": end,
                "timeZone": tz,
                "webLink": result.get("webLink"),
                "joinUrl": result.get("joinUrl"),
                "iCalUId": result.get("iCalUId")
            }),
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
            json.dumps({"error": "schedule-as-user failed", "detail": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
