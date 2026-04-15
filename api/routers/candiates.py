from fastapi import APIRouter

router = APIRouter()

@router.get("/")
def get_candidates():
    return {"message": "Candidates router working"}
