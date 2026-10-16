"""
Google OAuth 2.0 Token Verification & Email Allowlist Authentication Module.
"""

import os
import logging
from typing import Optional, Dict, Any, List
from fastapi import HTTPException, Security, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.oauth2 import id_token
from google.auth.transport import requests

logger = logging.getLogger("auth")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
raw_allowed_emails = os.getenv("ALLOWED_EMAILS", "").strip()

# Parse comma-separated emails
ALLOWED_EMAILS: List[str] = [
    e.strip().lower() for e in raw_allowed_emails.split(",") if e.strip()
]

security = HTTPBearer(auto_error=False)


def is_auth_enabled() -> bool:
    """Returns True if Google OAuth Client ID is configured."""
    return bool(GOOGLE_CLIENT_ID)


def verify_user_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Dict[str, Any]:
    """
    Validates Google ID Token from Authorization: Bearer <token>
    and checks user's email against the ALLOWED_EMAILS list.
    If GOOGLE_CLIENT_ID is not configured, allows requests for local development.
    """
    if not is_auth_enabled():
        # Local development / initial setup mode before OAuth client is configured
        return {
            "email": "dev-user@local",
            "name": "Local Developer",
            "authenticated": False,
            "auth_disabled": True,
        }

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in with Google.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_str = credentials.credentials
    try:
        id_info = id_token.verify_oauth2_token(
            token_str,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )

        email = id_info.get("email", "").strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token does not contain a verified email address."
            )

        # Check if email is in the allowlist
        if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
            logger.warning(f"Unauthorized access attempt by: {email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Account '{email}' is not on the authorized user list."
            )

        return {
            "email": email,
            "name": id_info.get("name", email),
            "picture": id_info.get("picture", ""),
            "authenticated": True,
            "auth_disabled": False,
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid Google ID Token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google ID Token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service unavailable."
        )
