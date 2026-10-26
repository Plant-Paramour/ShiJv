from fastapi import APIRouter, Header, HTTPException, Request

from ..schemas import FolderCreateModel, FolderPatchModel
from ..local_user import local_user

router = APIRouter(prefix="/v1/conversation-folders", tags=["conversation-folders"])


@router.get("")
def list_folders(request: Request):
    user = local_user(request)
    return {"items": request.app.state.users.list_folders(user["id"])}


@router.post("", status_code=201)
def create_folder(body: FolderCreateModel, request: Request):
    user = local_user(request)
    return request.app.state.users.create_folder(user["id"], body.name)


@router.patch("/{folder_id}")
def update_folder(folder_id: str, body: FolderPatchModel, request: Request):
    user = local_user(request)
    if not request.app.state.users.update_folder(folder_id, user["id"], body.name):
        raise HTTPException(status_code=404, detail="folder not found")
    return {"id": folder_id, "name": body.name}
