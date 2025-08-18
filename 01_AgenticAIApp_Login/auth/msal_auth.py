import msal
import requests
import streamlit as st
from typing import Optional, Dict, Any, List
import jwt
from datetime import datetime, timedelta
import json
import os
import tempfile
import hashlib

from config.settings import settings


class MSALAuthenticator:
    """Microsoft Authentication Library (MSAL) handler for Entra ID authentication."""
    
    def __init__(self):
        self.client_app = msal.ConfidentialClientApplication(
            client_id=settings.client_id,
            client_credential=settings.client_secret,
            authority=settings.authority
        )
        self.temp_dir = tempfile.gettempdir()
        
    def _get_flow_file_path(self, state: str) -> str:
        """Generate a file path for storing auth flow data."""
        # Create a hash of the state to avoid file name issues
        state_hash = hashlib.md5(state.encode()).hexdigest()
        return os.path.join(self.temp_dir, f"streamlit_auth_flow_{state_hash}.json")
    
    def _save_auth_flow(self, auth_flow: Dict[str, Any]) -> None:
        """Save auth flow to temporary file."""
        try:
            state = auth_flow.get("state", "")
            if state:
                file_path = self._get_flow_file_path(state)
                with open(file_path, 'w') as f:
                    json.dump(auth_flow, f)
        except Exception as e:
            pass  # Silently handle auth flow save errors
    
    def _load_auth_flow(self, state: str) -> Optional[Dict[str, Any]]:
        """Load auth flow from temporary file."""
        try:
            file_path = self._get_flow_file_path(state)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    auth_flow = json.load(f)
                return auth_flow
        except Exception as e:
            pass  # Silently handle auth flow load errors
        return None
    
    def _cleanup_auth_flow(self, state: str) -> None:
        """Clean up temporary auth flow file."""
        try:
            file_path = self._get_flow_file_path(state)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            pass  # Silently handle cleanup errors
        
    def get_auth_url(self) -> str:
        """Generate the authorization URL for user authentication."""
        # Use a simpler approach without PKCE to avoid session state issues
        auth_request = self.client_app.initiate_auth_code_flow(
            scopes=settings.scopes,
            redirect_uri=settings.redirect_uri
        )
        
        # Store the flow state in both session and file
        st.session_state["auth_flow"] = auth_request
        st.session_state["auth_state"] = auth_request.get("state", "")
        
        # Save to file as backup
        self._save_auth_flow(auth_request)
        
        return auth_request["auth_uri"]
    
    def acquire_token_by_auth_code(self, auth_code: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for access token."""
        try:
            # First, try to extract state from query parameters to locate the auth flow
            query_params = st.query_params
            callback_state = query_params.get("state", "")
            
            # Try to get the stored auth flow from session first
            auth_flow = st.session_state.get("auth_flow")
            
            # If not in session, try to load from file using the state
            if not auth_flow and callback_state:
                auth_flow = self._load_auth_flow(callback_state)
            
            if auth_flow and "code_verifier" in auth_flow:
                
                # Create the auth response from the callback
                auth_response = {
                    "code": auth_code,
                    "state": callback_state or auth_flow.get("state", "")
                }
                
                # Exchange the code for tokens using the stored flow
                result = self.client_app.acquire_token_by_auth_code_flow(
                    auth_code_flow=auth_flow,
                    auth_response=auth_response
                )
                
                # Clean up the temporary file
                if callback_state:
                    self._cleanup_auth_flow(callback_state)
                
            else:
                # Cannot proceed without complete auth flow - PKCE is required
                return None
            
            # Check result for errors
            if "error" in result:
                return None
            else:
                # Add expires_at timestamp for easier validation later
                if "expires_in" in result and result["expires_in"]:
                    expires_at = datetime.now().timestamp() + result["expires_in"]
                    result["expires_at"] = expires_at
                
            return result
            
        except Exception as e:
            return None
    
    def acquire_token_silent(self, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Silently acquire token using cached credentials."""
        try:
            result = self.client_app.acquire_token_silent(
                scopes=settings.scopes,
                account=account
            )
            
            if result and "access_token" in result:
                return result
            else:
                return None
                
        except Exception as e:
            return None
    
    def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetch user information from Microsoft Graph API."""
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Get user profile
            user_response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers=headers
            )
            
            if user_response.status_code != 200:
                return None
            
            user_info = user_response.json()
            
            # Get user's group memberships
            groups_response = requests.get(
                "https://graph.microsoft.com/v1.0/me/memberOf",
                headers=headers
            )
            
            groups = []
            if groups_response.status_code == 200:
                groups_data = groups_response.json()
                groups = [group["id"] for group in groups_data.get("value", [])]
            
            user_info["groups"] = groups
            return user_info
            
        except Exception as e:
            return None
    
    def logout(self) -> str:
        """Generate logout URL."""
        logout_url = f"{settings.authority}/oauth2/v2.0/logout"
        logout_url += f"?post_logout_redirect_uri={settings.redirect_uri}"
        return logout_url
    
    def is_token_valid(self, token_info: Dict[str, Any]) -> bool:
        """Check if the access token is still valid."""
        try:
            if not token_info or "access_token" not in token_info:
                return False
            
            # Check if token has expired
            # MSAL returns expires_in (seconds from token issue) and sometimes expires_at (timestamp)
            if "expires_at" in token_info:
                # Use expires_at if available (absolute timestamp)
                expires_at = token_info["expires_at"]
                current_time = datetime.now().timestamp()
                is_valid = current_time < (expires_at - 300)  # 5-minute buffer
                return is_valid
            elif "expires_in" in token_info:
                # Calculate expiration from expires_in (this is less reliable for stored tokens)
                # For now, assume token is valid if expires_in is present and > 0
                expires_in = token_info.get("expires_in", 0)
                return expires_in > 300  # Valid if more than 5 minutes left
            else:
                return False
            
        except Exception as e:
            return False


# Global authenticator instance
auth = MSALAuthenticator()
