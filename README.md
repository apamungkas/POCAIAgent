### Architecture

<img width="1019" height="840" alt="POC AI Agent" src="https://github.com/user-attachments/assets/ba212628-877b-4335-b7ca-de93bbff10d5" />


### Section 1: Deploying and Developing Foundation

<img width="568" height="840" alt="Section 1" src="https://github.com/user-attachments/assets/b2017883-3d1c-47a7-9713-8fa27a8ad880" />

In this step, we will make Frontend application that can call Backend (Azure Function App) and users can login with their Entra ID.

1. Create Resource Group
2. Create Resource: Bing Search
3. Create Resource: Azure AI Foundry
   + Deploy Model: GPT-4o
   + Create Agent: useragent
     + Add instruction
       + You are a helpful customer support agent. Always answer in a polite, professional tone.
       + Your job is to greet customer, and answer general questions.
       + If user asks about your name, answer with "User Agent".
     + Test in playground
   + Create Agent: adminagent
     + Add instruction
       + You are a helpful customer support agent. Always answer in a polite, professional tone.
       + Your job is to greet customer, and answer general questions.
       + Always use the Bing Search tool "bsagenticaidemo" when the user asks for real-time or current events information. Return the top result with title and summary.
       + If user asks about your name, answer with "Admin Agent".
       + If the user asks ‘what can you do?’, list the tool that you can access.
     + Attach Bing Search to agent knowledge
     + Test in playground
4. From Entra, create demo users and demo groups. Note the Group Object IDs.
5. From Entra, App registration, Register an application
    + Name: AgenticAIDemoFE	
	  + Redirect URI: Web - http://localhost:8501
	  + Note the client ID: <FE_Client_ID>
	  + From Certificates and secrets, create client secret.
	  + Note the secret value: <FE_Secret_Value>
    + Create claims in Token configuration
		  + Add groups claim, select all group types, and save
	  + Add API Permissions:
		  + Microsoft Graph > Delegated permissions > GroupMember.ReadAll
		  + Grant Admin consent
	  + Select token
		  + Authentication > select access tokens and id tokens
6. From Entra, App registration, Register an application
	  + Name: AgenticAIDemoBE
	  + Note the Client ID: <BE_Client_ID>
    + From Certificates and secrets, create client secret.
	    + Note the secret value: <BE_Secret_Value>
	  + Expose an API, create a scope:
		  + Scope name: Chat.Invoke
		  + Who can consent: Admins and users
		  + Display name: Invoke chat
		  + Description: Invoke chat
      + Note the scope: api://<BE_Client_ID>/Chat.Invoke
	  + Create App role:
		  + Display name: Admin
	      + Type: Users/Groups
		    + Value: Admin
		    + Description: Admins can use all tools
		  + Display name: User
		    + Type: Users/Groups
		    + Value: User
		    + Description: Users can use specific tool only
		  + Display name: Region2
		    + Type: Users/Groups
		    + Value: Region2
		    + Description: Region2
		  + Display name: Region3
		    + Type: Users/Groups
		    + Value: Region3
		    + Description: Region3
7. From App registration: AgenticAIDemoFE
    + Add permissions
      + APIs my organization uses, select AgenticAIDemoBE, select Chat – Chat.Invoke
		  + Grant admin consent
8. From Entra, Enterprise applications: AgenticAIDemoBE add Users and groups
	  + Add user and groups
	  + Select group Admin
    + Select role Admin
    + Add the same for User, Region2, and Region3 
9. Create Resource: Storage for App Function

    `az storage account create -n saagenticaidemo -g rg-agenticaidemo -l swedencentral --sku Standard_LRS --kind StorageV2`

10. Create Resource: Function App

    `az functionapp plan create --name "fpagenticaidemo" --resource-group "rg-agenticaidemo" --location "swedencentral" --sku B1 --is-linux`

    `az functionapp create --name "faagenticaidemo" --storage-account "saagenticaidemo" --resource-group "rg-agenticaidemo" --plan "fpagenticaidemo" --runtime python --runtime-version 3.11 --functions-version 4`

    `az functionapp config appsettings set --name "faagenticaidemo" --resource-group "rg-agenticaidemo" --settings AI_FOUNDRY_CONNECTION_STRING="<Agent_Connection_String>" AGENT_ID_USER="<Agent_ID_User>" AGENT_ID_ADMIN="<Agent_ID_Admin>" AGENT_ID="<Agent_ID_Default>"`

11. Configure Function App
    + Turn on System Assigned Managed Identity
12. Configure IAM for Function App from Resource Group
    + IAM > Add role assignment
    + Azure AI User > Managed Identity > Member: Function App faagenticaidemo
    + Review & assigned
13. Prepare AgenticAIFunction_Login workspace
14. Deploy to Azure Function App
15. Test Azure Function App

    `az functionapp function keys list -g rg-agenticaidemo -n faagenticaidemo --function-name chat --query default -o tsv`

    + Note the key: <Chat_Function_Key>

    `curl -X POST "https://faagenticaidemo.azurewebsites.net/api/chat?code=<Chat_Function_Key>" -H "Content-Type: application/json" -d '{"input":"Hello, can you help me?"}'`

16. Prepare AgenticAIApp workspace
17. Test login - Authentication from UI
18. Take screenshot

### Section 2: Connecting to APIM

<img width="335" height="281" alt="Section 2" src="https://github.com/user-attachments/assets/58496736-a0b1-4887-be2d-1f8619f70192" />

In this step, we will connect Frontend and Backend App to Azure API Management, so we can chat using different role, app will call different agent for each role.

1. Create Resource: API Management
	+ Create API from HTPP
		+ Display Name: AI Chat
		+ API URL Suffix:  ai-chat
	+ Settings, disable subscription required
	+ Add operation: chat
		+ Display name: chat
		+ URL: POST /chat
		+ Description: Send message to AI agent and receive response
		+ Request Description:
	+ Response: 200 OK
	+ Add API policy
		+ Adjust tenant-id, audience, group id, backend-service-base-url, function key
2. Prepare AgenticAIApp_ConnectedAPIM workspace
	+ Run streamlit app.py
	+ Login as User
		+ Take screenshot
	+ Login as Admin
		+ Take screenshot
	+ Ensure we have correct “aud” and “scp”. Copy the <token> from helper panel
3. Go to APIM API /ai-chat operation /chat
	+ Test with:
		+ Authorization: Bearer <token>
		+ Content-type: application/json
		+ Body: {“input”:”hi”}

<img width="940" height="598" alt="image" src="https://github.com/user-attachments/assets/d609a57c-4e4a-4e30-9022-038eb8680366" />

4. Prepare AgenticAIFunction_ToolsAuth workspace
	+ Deploy to Azure App Function
5. Run streamlit
	+ Login as User
		+ Ask “what is your name?”
		+ Ask “what can you do for me?”
		+ Take screen shot
	+ Login as Admin
		+ Ask “what is your name?”
		+ Ask “what can you do for me?”
		+ Take screenshot

### Section 3: Bing Search

<img width="503" height="336" alt="Section 3" src="https://github.com/user-attachments/assets/dcda9949-602e-45fc-b051-9aca03b4ece8" />

In this step, we will ask news from Bing Search. It will answer and provide links.

1. Prepare AgenticAIFunction_BingSearch workspace
	+ Deploy to Azure Function App
2. Prepare AgenticAIApp_BingSearch workspace
	+ Run streamlit app.py
	+ Ask “what the latest news in telco industry?”
	+ Confirm the link is working
	+ Take screenshot

### Section 4: Social Media and MCP Server

<img width="875" height="642" alt="Section 4" src="https://github.com/user-attachments/assets/48b06716-b890-4947-bcdd-9189dfdaded2" />

In this step, we will create YouTube MCP Server, so we can ask videos from YouTube API through MCP Server. It will answer and provide links.

1. From Azure API Management
	+ Create API from HTPP
		+ Display Name: YouTube
		+ Web Service URL: https://www.googleapis.com/youtube/v3
	+ Settings, disable subscription required.
	+ Add operation: search videos
		+ Display name: search videos
		+ URL: GET /search-videos
		+ Description: Search relevant videos based on keywords
		+ Query parameters:
			+ q
			+ maxResults, default 5
			+ type, default video
		+ Request Description
		+ Response: 200 OK
	+ Add policy
		+ Adjust YouTube API Key
	+ Test API in Azure APIM
		+ q: Telco news Indonesia
		+ maxResults: 5
		+ type: video
2. Create MCP Server in APIM
	+ Create MCP Server
	+ Expose an API as an MCP Server
	+ Backend MCP Server
		+ API: YouTube
		+ API operations: search videos
	+ MCP Server
		+ Display name: MCP YouTube
		+ Name: mcp-youtube
		+ Description: MCP server for YouTube APIs
	+ Note the server  URL: https://amagenticaidemo.azure-api.net/mcp/mcp
3. Smoke test with MCP Inspector
	+ From powershell: npx @modelcontextprotocol/inspector
		+ MCP Inspector window will appear
		+ Transport type: Streamable HTTP
		+ URL: https://amagenticaidemo.azure-api.net/mcp/mcp
		+ Connect
		+ List Tools, select searchVideos
		+ Fill parameters
			+ q: Telco news Indonesia
			+ maxResults: 5
			+ type: video
		+ Ensure response is success

<img width="940" height="501" alt="image" src="https://github.com/user-attachments/assets/8196860a-2a95-463a-bc36-7f433cd55e2d" />

4. Use MCP Server in VSCode
	+ Create new directory
	+ Add MCP Server
	+ Start MCP Server
	+ Set as agent, select tool: MCP YouTube
	+ Ask Copilot
5. Register MCP Server as actions/tools in Azure AI Foundry
	+ Go to useragent
	+ Add action: OpenAPI 3.0
		+ Name: YouTube
		+ Description: This action/tool is used for searching videos on YouTube based on keywords
		+ Authentication method: Anonymous
		+ Add OpenAPI schema
		+ Adjust the APIM URL
		+ If later using APIM subscription key, use the OpenAPI schema
	+ Modify Agent “useragent” instruction
		```
  		   -	You are a helpful customer support agent. Always answer in a polite, professional tone.
		   -	Your job is to greet customer, and answer general questions.
		   -	If user asks about your name, answer with "User Agent".
		   -	You can use "YouTube" tool.
		   -	If the user asks ‘what can you do?’, list the tool that you can access.
		   -	When the user asks to search YouTube, call the "YouTube" tool with { q, maxResults }.
6. Run streamlit app.py
	+ Ask “what the latest videos in telco industry?”
	+ Confirm the link is working
	+ Take screenshot

### Section 5: Send Email

<img width="537" height="336" alt="Section 5" src="https://github.com/user-attachments/assets/90f721c1-5df4-4844-880c-bd25ad819801" />

In this step, we create tool action for sending an email, we can send paragraph summary and send email via agent identity.

1. From Azure AI Foundry
	+ In adminagent, add new action with Logic App
	+ Type: Call external HTTP or HTTPS endpoints
	+ Action name: SendEmail
	+ Action description: This tool is used to send email to specific recipient email addresses with specific email subject and body/content
	+ HTTP Method: Post
	+ Describe how to invoke this tool: This tool should be used when the user asks to send an email.
	+ Create
2. From Logic App SendEmail
	+ Go to Logic App designer
	+ Action “When a HTTP request is received”
		+ Put request body JSON schema
	+ Add Action “Send an email (V2)”
		+ Sign in
		+ Put to
		+ Put subject
		+ Put body
	+ Remove HTTP action
	+ Action “Response”
		+ Put body
	+ Save
3. From Azure AI Foundry
	+ Set adminagent instruction:
		```
  		1. You are a helpful customer support agent. Always answer in a polite, professional tone.
		2. Your job is to greet customer, and answer general questions.
		3. Always use the Bing Search tool "bstelkomdemo01" when the user asks for real-time or current events information. Return the top result with title and summary.
		4. If user asks about your name, answer with "Admin Agent".
		5. If the user asks ‘what can you do?’, list the tool that you can access.
		6. If the user says "summarize this and send email": 
			- If the text is missing, ask: "Please paste the paragraphs to summarize."
			- When text is provided, produce: 
				a) SUBJECT: a short, specific line (max 8–12 words). No emojis. 
				b) BODY HTML: concise executive summary in HTML using <p>, <ul>, <li>, <b>. 
					- 5–7 bullets 
					- Bold key numbers/decisions 
					- No external CSS/images
		7. Ask for recipients if missing: 
			"Who should receive it? Please provide one or more email addresses."
		8. When you have BOTH the summary and recipients: 
			- Call SendEmail with JSON: 
				{ 
					"recipients": ["alice@contoso.com","bob@contoso.com"], "subject": "<your short subject>",
					"bodyHtml": "<!DOCTYPE html><html><body>...summary...</body></html>" 
				}
		9. After a successful tool call: 
			- Confirm: “Email sent to: alice@contoso.com; bob@contoso.com — Subject: <subject>”
			- Do not resend the full body unless the user asks.
		10. SendEmail_Tool Rules: 
			- Never use fields named HTTP_request_content or HTTP_URI.
			- Always send application/json with recipients[], subject, bodyHtml.
			- Keep follow-up questions minimal and only to fill missing required fields.
	+ Test in playground
		+ Prompt: “summary and send email”
4. Run streamlit app.py
	+ Ask with the same prompt
	+ Take screenshot

### Section 6: Schedule Meeting

<img width="537" height="336" alt="Section 6" src="https://github.com/user-attachments/assets/9b766200-a506-45de-a4fa-c428da3e985b" />

In this step, we create tool action for schedule a meeting, we can send meeting invitation to specific person.

1. From Azure AI Foundry
	+ In adminagent, add new action with Logic App
	+ Type: Call external HTTP or HTTPS endpoints
	+ Action name: ScheduleMeeting
	+ Action description: This tool is used to schedule meeting invitation to specific recipient email addresses with specific subject and body/content
	+ HTTP Method: Post
	+ Describe how to invoke this tool: This tool should be used when the user asks to schedule a meeting.
	+ Create
2. From Logic App ScheduleMeeting
	+ Go to Logic App designer
	+ Action “When a HTTP request is received”
		+ Put request body JSON schema
		+ Put Inputs
	+ Add Action “Compose”
		+ Name: Compose Required
3. Add Action “Send a Teams meeting”
		+ Sign in
		+ Put subject
		+ Put body event message content
		+ Put time zone: SE Asia Standard Time
		+ Put start time
		+ Put end time
		+ Put calendar ID in fx mode
		+ Add required attendees
	+ Remove HTTP action
	+ Action “Response”
		+ Put body
	+ Save
4. From Azure AI Foundry
	+ Set adminagent instruction:
		```
  		1. You are a helpful customer support agent. Always answer in a polite, professional tone.
		2. Your job is to greet customer, and answer general questions.
		3. Always use the Bing Search tool "bstelkomdemo01" when the user asks for real-time or current events information. Return the top result with title and summary.
		4. If user asks about your name, answer with "Admin Agent".
		5. If the user asks ‘what can you do?’, list the tool that you can access.
		6. If the user says "summarize this and send email": 
			- If the text is missing, ask: "Please paste the paragraphs to summarize."
			- When text is provided, produce: 
				a) SUBJECT: a short, specific line (max 8–12 words). No emojis. 
				b) BODY HTML: concise executive summary in HTML using <p>, <ul>, <li>, <b>. 
					- 5–7 bullets 
					- Bold key numbers/decisions 
					- No external CSS/images
		7. Ask for recipients if missing: 
			"Who should receive it? Please provide one or more email addresses."
		8. When you have BOTH the summary and recipients: 
			- Call SendEmail with JSON: 
				{ 
					"recipients": ["alice@contoso.com","bob@contoso.com"], "subject": "<your short subject>",
				 	"bodyHtml": "<!DOCTYPE html><html><body>...summary...</body></html>" 
				}
		9. After a successful tool call: 
			- Confirm: “Email sent to: alice@contoso.com; bob@contoso.com — Subject: <subject>”
			- Do not resend the full body unless the user asks.
		10. SendEmail_Tool Rules: 
			- Never use fields named HTTP_request_content or HTTP_URI.
			- Always send application/json with recipients[], subject, bodyHtml.
			- Keep follow-up questions minimal and only to fill missing required fields.
		11. When the user asks to “schedule/book/set up” a meeting, extract: 
			- requiredAttendees (emails, ≥1), subject, start+end (or start+duration)
			- Optional: optionalAttendees, location (default “Microsoft Teams”), calendarId (default “Calendar”)
			- timeZone default “SE Asia Standard Time” (Jakarta)
		12. ScheduleMeeting_Tool Rules: 
			- If any critical info is missing, ask one concise follow-up listing all missing items.
			- Use ISO local times YYYY-MM-DDTHH:mm:ss. If only duration is given, compute end.
			- Build a short HTML body (convert any line breaks/markdown to HTML).
			- Validate: at least one recipient, valid emails (@ present), and end > start.
		13. Call ScheduleMeeting_Tool once with: 
			{ 
				"subject": "<title>", 
				"body": "<HTML agenda/notes>", 
				"timeZone": "SE Asia Standard Time", 
				"start": "YYYY-MM-DDTHH:mm:ss", 
				"end": "YYYY-MM-DDTHH:mm:ss", 
				"calendarId": "Calendar", 
				"requiredAttendees": ["a@contoso.com"], "optionalAttendees": [], 
				"location": "Microsoft Teams" }
		14. After the tool returns: 
			- On success: confirm subject, date/time with timezone, attendees, and any join/weblink.
			- On error: show the short error and ask for fixes.

5. Test in playground
	+ Prompt: “Schedule a meeting with <someone_email>@<email_domain> tomorrow 15:00–15:30 WIB about Fabric Q3 review, online (Teams). Agenda: KPI dashboard; Action items.”
6. Run streamlit app.py
	+ Ask the same prompt
	+ Take screenshot


