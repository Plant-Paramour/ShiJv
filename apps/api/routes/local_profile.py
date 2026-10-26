from fastapi import APIRouter, Request
from pydantic import BaseModel, Field


router = APIRouter(prefix="/v1/local-profile", tags=["local-profile"])


class LocalProfilePatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


@router.get("")
def get_local_profile(request: Request):
    profile = request.app.state.users.local_profile()
    return {"created": profile is not None, "profile": profile}


@router.post("", status_code=200)
def get_existing_local_profile(request: Request):
    # Older clients can call this safely; request nicknames cannot create users.
    return get_local_profile(request)


@router.patch("")
def update_local_profile(body: LocalProfilePatch, request: Request):
    profile = request.app.state.users.local_profile()
    updated = request.app.state.users.update_profile(profile["id"], {"display_name": body.display_name})
    return {"created": True, "profile": updated}
