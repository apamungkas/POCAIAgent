import streamlit as st
from dotenv import load_dotenv
import os
import json
import base64
import requests

# Load environment variables
load_dotenv()

# Import our modules
from auth.msal_auth import auth
from auth.rbac import rbac, UserRole
# from ai_agent.foundry_client import ai_agent


def peek_jwt(token: str):
    """Decode JWT payload for debugging."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {"_error": "Not a JWT"}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return payload
    except Exception as e:
        return {"_error": str(e)}


class AzureAIFoundryApp:
    """Main Streamlit application for Azure AI Foundry with Entra ID authentication."""
    
    def __init__(self):
        self.setup_page_config()
        self.load_custom_css()
        
    def setup_page_config(self):
        st.set_page_config(
            page_title="AI Chatbot Demo",
            page_icon="💬",
            layout="wide",
            initial_sidebar_state="expanded"
        )
    
    def load_custom_css(self):
        try:
            with open("static/style.css") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        except FileNotFoundError:
            pass  
    
    def display_header(self):
        st.markdown("""
        <div class="main-header">
            <h1>Telkom AI Agent</h1>
            <p>Secure AI Assistant with Microsoft Entra ID Authentication</p>
        </div>
        """, unsafe_allow_html=True)
    
    def handle_authentication_flow(self):
        query_params = st.query_params
        
        if "code" in query_params:
            auth_code = query_params["code"]
            
            with st.spinner("Authenticating..."):
                token_result = auth.acquire_token_by_auth_code(auth_code)
                
                if token_result and "access_token" in token_result:
                    # Save tokens into session
                    st.session_state["access_token"] = token_result["access_token"]
                    st.session_state["token_info"] = token_result
                    st.session_state["id_token_claims"] = token_result.get("id_token_claims", {})

                    # Get user info (Graph if available; otherwise from claims)
                    user_info = auth.get_user_info(token_result["access_token"])
                    if not user_info:
                        claims = token_result.get("id_token_claims", {})
                        user_info = {
                            "displayName": claims.get("name", "Unknown"),
                            "userPrincipalName": claims.get("preferred_username", ""),
                            "groups": claims.get("groups", [])
                        }

                    if user_info:
                        st.session_state["authenticated"] = True
                        st.session_state["user_info"] = user_info
                        st.session_state["user_role"] = rbac.determine_user_role(user_info.get("groups", []))
                        
                        st.query_params.clear()
                        st.rerun()
                    else:
                        st.error("Failed to retrieve user information.")
                        return False
                else:
                    st.error("Authentication failed. Please try again.")
                    return False
        return True

    # --- NEW: make sure access_token exists whenever token_info is valid ---
    def ensure_access_token(self):
        tok = st.session_state.get("access_token")
        if tok:
            return
        token_info = st.session_state.get("token_info", {})
        if token_info and "access_token" in token_info:
            st.session_state["access_token"] = token_info["access_token"]

    def display_login_page(self):
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col2:
            st.markdown("""
            <div class="auth-container">
                <h2>Authentication Required</h2>
                <p>Please sign in with your Microsoft account to access the AI Agent.</p>
            </div>
            """, unsafe_allow_html=True)
            
            if st.button("Sign in with Microsoft", type="primary", use_container_width=True):
                auth_url = auth.get_auth_url()
                st.markdown(f'<meta http-equiv="refresh" content="0;url={auth_url}">', unsafe_allow_html=True)
    
    def display_user_info_sidebar(self):
        user_info = st.session_state.get("user_info", {})
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        
        with st.sidebar:
            st.markdown("### User Information")
            st.markdown(f"""
            <div class="sidebar-info">
                <p><strong>Name:</strong> {user_info.get('displayName', 'Unknown')}</p>
                <p><strong>Role:</strong> {rbac.get_role_display_name(user_role)}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Debug: show JWT claims + raw token (copyable)
            access_token = st.session_state.get("access_token")
            with st.expander("Access Token (debug)"):
                if access_token:
                    claims = peek_jwt(access_token)
                    st.markdown("**Claims:**")
                    st.json({k: claims.get(k) for k in ["aud", "scp", "roles", "tid", "iss", "groups"]})
                    st.markdown("**Raw token:**")
                    st.text_area("access_token", value=access_token, height=120)
                else:
                    st.info("No access token in session yet.")
                    if st.button("Load token from session"):
                        self.ensure_access_token()
                        st.rerun()
            
            st.markdown("---")
            st.markdown("### Chat Controls")
            
            if st.button("Clear Chat", use_container_width=True):
                if "chat_history" in st.session_state:
                    st.session_state["chat_history"] = []
                st.rerun()
            
            if st.button("Logout", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                logout_url = auth.logout()
                st.markdown(f'<meta http-equiv="refresh" content="0;url={logout_url}">', unsafe_allow_html=True)
    
    def display_main_chat_interface(self):
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []
        
        st.markdown("### AI Agent")
        st.info(f"**Access Level:** {rbac.get_role_display_name(user_role)}")
        
        chat_container = st.container()
        with chat_container:
            for message in st.session_state["chat_history"]:
                with st.chat_message(message["role"]):
                    st.write(message["content"])
        
        user_message = st.chat_input("Ask me anything...", key="chat_input")
        
        if user_message:
            st.session_state["chat_history"].append({"role": "user", "content": user_message})
            with st.chat_message("user"):
                st.write(user_message)
            
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        apim_base = os.getenv("API_BASE", "")
                        apim_endpoint = f"{apim_base}/chat"
                        st.caption(f"Calling: {apim_endpoint}")  # small hint for debugging

                        headers = {
                            "Authorization": f"Bearer {st.session_state.get('access_token')}",
                            "Content-Type": "application/json"
                        }
                        payload = {"input": user_message}
                        if st.session_state.get("thread_id"):
                            payload["thread_id"] = st.session_state["thread_id"]

                        resp = requests.post(apim_endpoint, json=payload, headers=headers, timeout=60)

                        if resp.status_code == 200:
                            data = resp.json()
                            ai_response = data.get("answer", "No response from AI")
                            if data.get("thread_id"):
                                st.session_state["thread_id"] = data["thread_id"]
                        else:
                            ai_response = f"Error {resp.status_code}: {resp.text}"

                        st.write(ai_response)
                        st.session_state["chat_history"].append({"role": "assistant", "content": ai_response})
                        
                    except Exception as e:
                        error_msg = f"Error: {str(e)}"
                        st.error(error_msg)
                        st.session_state["chat_history"].append({"role": "assistant", "content": error_msg})
            st.rerun()
    
    def check_authentication_status(self):
        if "authenticated" not in st.session_state:
            return False
        
        token_info = st.session_state.get("token_info", {})
        if not auth.is_token_valid(token_info):
            user_info = st.session_state.get("user_info", {})
            if user_info:
                account = {
                    "username": user_info.get("userPrincipalName", ""),
                    "home_account_id": user_info.get("id", "")
                }
                refreshed_token = auth.acquire_token_silent(account)
                if refreshed_token:
                    st.session_state["token_info"] = refreshed_token
                    st.session_state["access_token"] = refreshed_token["access_token"]
                    return True
                else:
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    return False
        else:
            # ensure access_token present if token is valid
            self.ensure_access_token()
        return True
    
    def run(self):
        self.display_header()
        if not self.handle_authentication_flow():
            return
        if not self.check_authentication_status():
            self.display_login_page()
            return
        if st.session_state.get("user_role", UserRole.UNAUTHORIZED) == UserRole.UNAUTHORIZED:
            st.error("Access Denied: You don't have the required permissions to use this application.")
            if st.button("Logout"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
            return
        self.display_user_info_sidebar()
        self.display_main_chat_interface()


def main():
    try:
        app = AzureAIFoundryApp()
        app.run()
    except Exception as e:
        st.error(f"Application error: {str(e)}")


if __name__ == "__main__":
    main()
