import msal
import requests
import streamlit as st
from typing import Optional, Dict, Any
from datetime import datetime
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
        except Exception:
            pass  # Silently handle auth flow save errors
    
    def _load_auth_flow(self, state: str) -> Optional[Dict[str, Any]]:
        """Load auth flow from temporary file."""
        try:
            file_path = self._get_flow_file_path(state)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    auth_flow = json.load(f)
                return auth_flow
        except Exception:
            pass  # Silently handle auth flow load errors
        return None
    
    def _cleanup_auth_flow(self, state: str) -> None:
        """Clean up temporary auth flow file."""
        try:
            file_path = self._get_flow_file_path(state)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass  # Silently handle cleanup errors
        
    def get_auth_url(self) -> str:
        """Generate the authorization URL for user authentication."""
        # IMPORTANT: settings.scopes must include your API scope + openid/profile/offline_access
        auth_request = self.client_app.initiate_auth_code_flow(
            scopes=settings.scopes,
            redirect_uri=settings.redirect_uri
        )
        # Store the flow state in both session and file
        st.session_state["auth_flow"] = auth_request
        st.session_state["auth_state"] = auth_request.get("state", "")
        self._save_auth_flow(auth_request)
        return auth_request["auth_uri"]
    
    def acquire_token_by_auth_code(self, auth_code: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for access token."""
        try:
            # Get 'state' from callback to find saved flow
            query_params = st.query_params
            callback_state = query_params.get("state", "")
            auth_flow = st.session_state.get("auth_flow")

            if not auth_flow and callback_state:
                auth_flow = self._load_auth_flow(callback_state)
            
            if auth_flow and "code_verifier" in auth_flow:
                auth_response = {
                    "code": auth_code,
                    "state": callback_state or auth_flow.get("state", "")
                }
                result = self.client_app.acquire_token_by_auth_code_flow(
                    auth_code_flow=auth_flow,
                    auth_response=auth_response
                )
                if callback_state:
                    self._cleanup_auth_flow(callback_state)
            else:
                # Flow context missing → cannot complete
                return None
            
            # Check result for errors
            if not result or "error" in result:
                return None

            # Add absolute expiry stamp
            if result.get("expires_in"):
                result["expires_at"] = datetime.now().timestamp() + result["expires_in"]

            # TIP: keep id_token_claims handy for UI/RBAC (no Graph call needed)
            # result["id_token_claims"] is already returned by MSAL; your Streamlit app can stash it.
            return result
            
        except Exception:
            return None
    
    def acquire_token_silent(self, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Silently acquire token using cached credentials."""
        try:
            result = self.client_app.acquire_token_silent(
                scopes=settings.scopes,  # must match the same API scope set used during auth
                account=account
            )
            if result and "access_token" in result:
                return result
            return None
        except Exception:
            return None

    # ---------- Minimal additions for APIM flow ----------
    def get_id_token_claims(self, token_result: Dict[str, Any]) -> Dict[str, Any]:
        """Return ID token claims for UI/RBAC (name, upn, oid, groups)."""
        return token_result.get("id_token_claims", {}) if token_result else {}

    def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        DO NOT call Microsoft Graph with the API access token.
        For APIM flow, use ID token claims that you stored after login.
        This keeps your client independent of Graph and avoids 401s.
        """
        claims = st.session_state.get("id_token_claims") or {}
        if not claims:
            return None
        # Normalize to your existing sidebar expectations
        return {
            "displayName": claims.get("name"),
            "userPrincipalName": claims.get("preferred_username"),
            "id": claims.get("oid"),
            "groups": claims.get("groups", []),  # only present if you configured group claims
        }
    
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
            if "expires_at" in token_info:
                current_time = datetime.now().timestamp()
                return current_time < (token_info["expires_at"] - 300)  # 5-min buffer
            elif "expires_in" in token_info:
                return token_info.get("expires_in", 0) > 300
            return False
        except Exception:
            return False


# Global authenticator instance
auth = MSALAuthenticator()
