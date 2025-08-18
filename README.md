### Architecture

<img width="1019" height="840" alt="POC AI Agent" src="https://github.com/user-attachments/assets/ba212628-877b-4335-b7ca-de93bbff10d5" />


### Section 1: Deploying and Developing Foundation

<img width="568" height="840" alt="Section 1" src="https://github.com/user-attachments/assets/2a96ed68-ee26-4cd9-a515-a6fde578f505" />

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

<img width="371" height="515" alt="Section 2" src="https://github.com/user-attachments/assets/b208bb87-45f2-4c2f-a173-5bb35b7148c8" />

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







