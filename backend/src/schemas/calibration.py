from pydantic import BaseModel,Field
from typing import Optional


class CalibrationProjectInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    source_filename:str=Field(min_length=3,max_length=120)
    description:str=Field(default='',max_length=2000)
    baseline_instrument:str=Field(min_length=1,max_length=100)


class CalibrationProjectUpdate(BaseModel):
    baseline_instrument:Optional[str]=Field(default=None,min_length=1,max_length=100)
    description:Optional[str]=Field(default=None,max_length=2000)


class InstrumentInput(BaseModel):
    name:str=Field(min_length=1,max_length=100)


class PairInput(BaseModel):
    a:str=Field(min_length=1,max_length=100)
    b:str=Field(min_length=1,max_length=100)
    lower:float
    upper:float
    basis:str=Field(default='',max_length=1000)


class CalibrationRunInput(BaseModel):
    project_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=120)
    idempotency_key:str=Field(min_length=8,max_length=160)


class PublishInput(BaseModel):
    note:str=Field(min_length=5,max_length=2000)
    scope:str=Field(default='',max_length=500)
