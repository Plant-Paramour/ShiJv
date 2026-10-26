from __future__ import annotations
import asyncio
import time
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from ..job_repository import JobOwnershipError
from ..schemas import WorkerClaimModel, WorkerCompleteModel, WorkerFailModel, WorkerIdentityModel, WorkerCandidateModel
router = APIRouter(prefix='/local/v1', tags=['gpu-worker'])

@router.post('/workers/claim')
async def claim_job(body: WorkerClaimModel, request: Request):
    deadline = asyncio.get_running_loop().time() + body.wait_seconds
    while True:
        job = request.app.state.jobs.claim(body.worker_id, body.capabilities, lease_seconds=request.app.state.settings.worker_lease_seconds, kinds=tuple(body.job_kinds) if body.job_kinds else None, job_id=body.job_id)
        if job is not None:
            return job.to_dict(include_request=True)
        if asyncio.get_running_loop().time() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT, headers={'Retry-After': '1'})
        await asyncio.sleep(1)

@router.post('/jobs/{job_id}/heartbeat')
def heartbeat(job_id: str, body: WorkerIdentityModel, request: Request):
    try:
        cancel_requested = request.app.state.jobs.heartbeat(job_id, body.worker_id, lease_seconds=request.app.state.settings.worker_lease_seconds)
    except JobOwnershipError as exc:
        raise HTTPException(status_code=409, detail='job ownership lost') from exc
    return {'cancel_requested': cancel_requested}

@router.post('/jobs/{job_id}/cancelled', status_code=status.HTTP_204_NO_CONTENT)
def acknowledge_cancel(job_id: str, body: WorkerIdentityModel, request: Request):
    try:
        request.app.state.jobs.acknowledge_cancel(job_id, body.worker_id)
    except JobOwnershipError as exc:
        raise HTTPException(status_code=409, detail='job ownership lost') from exc

@router.post('/jobs/{job_id}/complete', status_code=status.HTTP_204_NO_CONTENT)
def complete(job_id: str, body: WorkerCompleteModel, request: Request):
    try:
        request.app.state.jobs.complete(job_id, body.worker_id, body.result)
    except JobOwnershipError as exc:
        raise HTTPException(status_code=409, detail='job ownership lost') from exc

@router.post('/jobs/{job_id}/fail', status_code=status.HTTP_204_NO_CONTENT)
def fail(job_id: str, body: WorkerFailModel, request: Request):
    try:
        request.app.state.jobs.fail(job_id, body.worker_id, body.code, body.message, retryable=body.retryable)
    except JobOwnershipError as exc:
        raise HTTPException(status_code=409, detail='job ownership lost') from exc

@router.post('/jobs/{job_id}/candidates', status_code=status.HTTP_204_NO_CONTENT)
def candidate_update(job_id: str, body: WorkerCandidateModel, request: Request):
    try:
        request.app.state.jobs.update_candidate(job_id, body.ordinal, worker_id=body.worker_id, **body.model_dump(exclude={'worker_id', 'ordinal'}, exclude_none=True))
    except JobOwnershipError as exc:
        raise HTTPException(status_code=409, detail='job ownership lost') from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
