import os, json, re
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# Initialize client and agent(s)
client = None
init_error = None

try:
    # Endpoint-style configuration
    PROJECT_ENDPOINT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
    SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID")
    RESOURCE_GROUP  = os.environ.get("AZURE_RESOURCE_GROUP")
    PROJECT_NAME    = os.environ.get("AI_FOUNDRY_PROJECT_NAME")

    # Role-based agents (+ fallback)
    AGENT_ID_DEFAULT = os.environ.get("AGENT_ID")        # optional fallback
    AGENT_ID_USER    = os.environ.get("AGENT_ID_USER")   # user agent
    AGENT_ID_ADMIN   = os.environ.get("AGENT_ID_ADMIN")  # admin agent

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
        # We pass assistant_id per call; no upfront agent fetch here.

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

def _ensure_thread(thread_id: str | None) -> str:
    if thread_id:
        return thread_id
    # NEW: threads live under client.agents.threads
    t = client.agents.threads.create()
    return getattr(t, "id", t.get("id") if isinstance(t, dict) else t)

def _add_user_message(thread_id: str, text: str):
    # NEW: messages live under client.agents.messages
    client.agents.messages.create(
        thread_id=thread_id,
        role="user",
        content=text
    )

def _run_and_wait(thread_id: str, assistant_id: str) -> str:
    # NEW: runs live under client.agents.runs
    run = client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=assistant_id
    )

    # If run failed, surface error
    if getattr(run, "status", "").lower() == "failed":
        last_error = getattr(run, "last_error", None)
        return f"(run failed: {last_error})" if last_error else "(run failed)"

    # List messages in the thread and return the latest assistant text
    messages = client.agents.messages.list(thread_id=thread_id)
    for msg in messages:
        if getattr(msg, "role", None) == "assistant" and getattr(msg, "text_messages", None):
            text_value = msg.text_messages[-1].text.value
            return re.sub(r'【[^】]+】', '', text_value)

    return "(no assistant message)"

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
        answer = _run_and_wait(thread_id, assistant_id=agent_id)
        
        # Include which agent was used (handy for debugging)
        return func.HttpResponse(
            json.dumps({"answer": answer, "thread_id": thread_id, "agent_id": agent_id}),
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
