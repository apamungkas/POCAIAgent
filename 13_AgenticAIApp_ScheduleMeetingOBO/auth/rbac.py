from enum import Enum
from typing import List, Dict, Any
from config.settings import settings


class UserRole(Enum):
    """User roles in the application."""
    ADMIN = "admin"
    USER = "user"
    UNAUTHORIZED = "unauthorized"


class RBACManager:
    """Role-Based Access Control manager."""
    
    def __init__(self):
        self.ai_context_by_role = {
            UserRole.ADMIN: {
                "system_prompt": """You are an AI assistant with full access to organizational data. 
                You can provide detailed information, sensitive data insights, and administrative guidance. 
                The user has administrative privileges.""",
            },
            UserRole.USER: {
                "system_prompt": """You are an AI assistant providing standard business information. 
                You can help with general queries and provide information relevant to regular users. 
                Avoid sharing sensitive or administrative data.""",
            },
            UserRole.UNAUTHORIZED: {
                "system_prompt": "Access denied. Please authenticate first.",
            }
        }
    
    def determine_user_role(self, user_groups: List[str]) -> UserRole:
        """Determine user role based on Entra ID group membership."""
        if not user_groups:
            return UserRole.UNAUTHORIZED
        
        # Check for admin role first (highest priority)
        if settings.admin_group_id in user_groups:
            return UserRole.ADMIN
        
        # Check for user role
        if settings.user_group_id in user_groups:
            return UserRole.USER
        
        # If user is not in Admin or User groups, they are unauthorized
        return UserRole.UNAUTHORIZED
    
    def get_ai_context(self, role: UserRole) -> Dict[str, Any]:
        """Get AI context configuration for a specific role."""
        return self.ai_context_by_role.get(role, self.ai_context_by_role[UserRole.UNAUTHORIZED])
    
    def get_role_display_name(self, role: UserRole) -> str:
        """Get display-friendly role name."""
        role_names = {
            UserRole.ADMIN: "Administrator",
            UserRole.USER: "User",
            UserRole.UNAUTHORIZED: "Unauthorized"
        }
        return role_names.get(role, "Unknown")


# Global RBAC manager instance
rbac = RBACManager()
