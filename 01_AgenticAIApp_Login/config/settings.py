from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    # Azure Entra ID Configuration
    tenant_id: str
    client_id: str
    client_secret: str
    
    # Azure AI Foundry Configuration
    # azure_ai_foundry_connection_string: str
    # azure_ai_foundry_agent_id: str
    
    # Application Configuration
    redirect_uri: str = "http://localhost:8501/"
    secret_key: str
    
    # Role Configuration
    admin_group_id: str
    user_group_id: str
    
    # Region Configuration
    region2_group_id: str
    region3_group_id: str
    
    @property
    def authority(self) -> str:
        """Azure authority URL."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"
    
    @property
    def scopes(self) -> List[str]:
        """OAuth scopes required for the application."""
        return [
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/GroupMember.Read.All"
        ]


# Global settings instance
settings = Settings()
