import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import our modules
from auth.msal_auth import auth
from auth.rbac import rbac, UserRole

class AzureAIFoundryApp:
    """Main Streamlit application for Azure AI Foundry with Entra ID authentication."""
    
    def __init__(self):
        self.setup_page_config()
        self.load_custom_css()
        
    def setup_page_config(self):
        """Configure Streamlit page settings."""
        st.set_page_config(
            page_title="AI Chatbot Demo",
            page_icon="💬",
            layout="wide",
            initial_sidebar_state="expanded"
        )
    
    def load_custom_css(self):
        """Load custom CSS styling."""
        try:
            with open("static/style.css") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        except FileNotFoundError:
            pass  # CSS file is optional
    
    def display_header(self):
        """Display the main application header."""
        st.markdown("""
        <div class="main-header">
            <h1>Telkom AI Agent</h1>
            <p>Secure AI Assistant with Microsoft Entra ID Authentication</p>
        </div>
        """, unsafe_allow_html=True)
    
    def handle_authentication_flow(self):
        """Handle the OAuth authentication flow."""
        # Check if we're returning from OAuth flow
        query_params = st.query_params
        
        if "code" in query_params:
            auth_code = query_params["code"]
            
            with st.spinner("Authenticating..."):
                token_result = auth.acquire_token_by_auth_code(auth_code)
                
                if token_result and "access_token" in token_result:
                    # Get user information
                    user_info = auth.get_user_info(token_result["access_token"])
                    
                    if user_info:
                        # Store user session
                        st.session_state["authenticated"] = True
                        st.session_state["token_info"] = token_result
                        st.session_state["user_info"] = user_info
                        st.session_state["user_role"] = rbac.determine_user_role(user_info.get("groups", []))
                        
                        # Clear query params to prevent re-authentication
                        st.query_params.clear()
                        st.rerun()
                    else:
                        st.error("Failed to retrieve user information.")
                        return False
                else:
                    st.error("Authentication failed. Please try again.")
                    return False
        
        return True
    
    def display_login_page(self):
        """Display the login page for unauthenticated users."""
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
        """Display simplified user information in the sidebar for chatbot demo."""
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
            
            st.markdown("---")
            
            # Simple chat controls
            st.markdown("### Chat Controls")
            
            if st.button("Clear Chat", use_container_width=True):
                # ai_agent.clear_conversation()
                if "chat_history" in st.session_state:
                    st.session_state["chat_history"] = []
                st.rerun()
            
            if st.button("Logout", use_container_width=True):
                # Clear session state
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                # Redirect to logout URL
                logout_url = auth.logout()
                st.markdown(f'<meta http-equiv="refresh" content="0;url={logout_url}">', unsafe_allow_html=True)
    
    def display_main_chat_interface(self):
        """Display the simplified chatbot interface."""
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        user_info = st.session_state.get("user_info", {})
        
        # Initialize chat history
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []
        
        # Main content area
        st.markdown("### AI Agent")
        
        # Show role-based information in a more concise way
        st.info(f"**Access Level:** {rbac.get_role_display_name(user_role)}")
        
        # Chat container
        chat_container = st.container()
        
        # Display chat history
        with chat_container:
            for message in st.session_state["chat_history"]:
                if message["role"] == "user":
                    with st.chat_message("user"):
                        st.write(message["content"])
                else:
                    with st.chat_message("assistant"):
                        st.write(message["content"])
        
        # Chat input
        user_message = st.chat_input("Ask me anything...", key="chat_input")
        
        if user_message:
            # Add user message to history
            st.session_state["chat_history"].append({
                "role": "user",
                "content": user_message
            })
            
            # Display user message immediately
            with st.chat_message("user"):
                st.write(user_message)
            
            # Get AI response
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        # ai_response = ai_agent.chat_with_agent(
                        #     message=user_message,
                        #     user_role=user_role,
                        #     user_info=user_info
                        # )
                        
                        st.write(ai_response)
                        
                        # Add AI response to history
                        st.session_state["chat_history"].append({
                            "role": "assistant",
                            "content": ai_response
                        })
                        
                    except Exception as e:
                        error_msg = "I apologize, but I'm experiencing technical difficulties. Please try again."
                        st.error(f"Error: {str(e)}")
                        st.write(error_msg)
                        
                        st.session_state["chat_history"].append({
                            "role": "assistant",
                            "content": error_msg
                        })
            
            st.rerun()
    
    def check_authentication_status(self):
        """Check if user is authenticated and token is valid."""        
        if "authenticated" not in st.session_state:
            return False
        
        token_info = st.session_state.get("token_info", {})
        
        if not auth.is_token_valid(token_info):
            # Try to refresh token
            user_info = st.session_state.get("user_info", {})
            if user_info:
                account = {
                    "username": user_info.get("userPrincipalName", ""),
                    "home_account_id": user_info.get("id", "")
                }
                
                refreshed_token = auth.acquire_token_silent(account)
                if refreshed_token:
                    st.session_state["token_info"] = refreshed_token
                    return True
                else:
                    # Token refresh failed, need to re-authenticate
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    return False
        
        return True
    
    def run(self):
        """Main application runner."""
        self.display_header()
        
        # Handle OAuth callback
        if not self.handle_authentication_flow():
            return
        
        # Check authentication status
        if not self.check_authentication_status():
            self.display_login_page()
            return
        
        # Check user role authorization
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        if user_role == UserRole.UNAUTHORIZED:
            st.error("🚫 Access Denied: You don't have the required permissions to use this application.")
            st.info("Please contact your administrator to get access to the appropriate groups.")
            
            if st.button("🚪 Logout"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
            return
        
        # Display authenticated user interface
        self.display_user_info_sidebar()
        self.display_main_chat_interface()


def main():
    """Application entry point."""
    try:
        app = AzureAIFoundryApp()
        app.run()
    except Exception as e:
        st.error(f"Application error: {str(e)}")
        st.info("Please check your configuration and try again.")


if __name__ == "__main__":
    main()
