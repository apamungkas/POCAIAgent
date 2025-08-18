import os, json, re
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# Initialize client and agent
client = None
agent = None
init_error = None

try:
    PROJECT_ENDPOINT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
    SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID")
    RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP")
    PROJECT_NAME = os.environ.get("AI_FOUNDRY_PROJECT_NAME")
    AGENT_ID = os.environ.get("AGENT_ID")
    
    if not PROJECT_ENDPOINT:
        init_error = "AI_FOUNDRY_PROJECT_ENDPOINT environment variable not set"
    elif not SUBSCRIPTION_ID or not RESOURCE_GROUP or not PROJECT_NAME:
        init_error = "AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AI_FOUNDRY_PROJECT_NAME must be set"
    elif not AGENT_ID:
        init_error = "AGENT_ID environment variable not set"
    else:
        credential = DefaultAzureCredential()
        client = AIProjectClient(
            credential=credential,
            endpoint=PROJECT_ENDPOINT,
            subscription_id=SUBSCRIPTION_ID,
            resource_group_name=RESOURCE_GROUP,
            project_name=PROJECT_NAME
        )
        agent = client.agents.get_agent(AGENT_ID)
        
        if not agent:
            init_error = f"Agent with ID {AGENT_ID} not found"
            client = None
            agent = None
except Exception as e:
    init_error = f"Failed to initialize Azure AI Foundry client: {str(e)}"
    client = None
    agent = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

def _ensure_thread(thread_id: str | None) -> str:
    if thread_id:
        return thread_id
    t = client.agents.threads.create()
    return t.id

def _add_user_message(thread_id: str, text: str):
    client.agents.messages.create(
        thread_id=thread_id,
        role="user",
        content=text
    )

def _run_and_wait(thread_id: str) -> str:
    run = client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent.id
    )
    
    if run.status == "failed":
        return f"(run failed: {run.last_error})"

    messages = client.agents.messages.list(thread_id=thread_id)
    
    for msg in messages:
        if getattr(msg, "role", None) == "assistant" and getattr(msg, "text_messages", None):
            text_value = msg.text_messages[-1].text.value
            return re.sub(r'【[^】]+】', '', text_value)
    
    return "(no assistant message)"

@app.route(route="chat", methods=[func.HttpMethod.POST])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    # Check if there was an initialization error
    if init_error:
        return func.HttpResponse(
            json.dumps({"error": "Initialization failed", "detail": init_error}),
            status_code=503,
            mimetype="application/json"
        )

    if not client or not agent:
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
        
        thread_id = _ensure_thread(body.get("thread_id"))
        _add_user_message(thread_id, text)
        answer = _run_and_wait(thread_id)
        
        return func.HttpResponse(
            json.dumps({"answer": answer, "thread_id": thread_id}),
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
