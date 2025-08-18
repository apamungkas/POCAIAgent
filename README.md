### Architecture

<img width="1019" height="840" alt="POC AI Agent" src="https://github.com/user-attachments/assets/ba212628-877b-4335-b7ca-de93bbff10d5" />


### Section 1: Deploying and Developing Foundation

<img width="532" height="516" alt="Section 1" src="https://github.com/user-attachments/assets/3ead259a-ae1c-4ec1-ad1a-30f1bd3d1f34" />

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
18. Take screenshot.


