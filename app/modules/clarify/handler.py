from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
router=APIRouter()
@router.get('/clarify')
def page(request:Request):
    return render(request,'clarify')
@router.post('/api/clarify')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload,until=6)
