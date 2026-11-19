from fastapi import APIRouter,Depends
from fastapi.responses import Response
from src.config.database import get_db
from src.middlewares.auth import current_user,writer,admin
from src.schemas.calibration import (
    CalibrationProjectInput,
    CalibrationProjectUpdate,
    InstrumentInput,
    PairInput,
    CalibrationRunInput,
    PublishInput,
)
from src.controllers import calibration
from src.services import calibration as calibration_service

router=APIRouter(prefix='/calibration')


@router.get('/projects')
def projects(db=Depends(get_db),user=Depends(current_user)): return calibration.list_projects(db)


@router.post('/projects')
def create_project(data:CalibrationProjectInput,db=Depends(get_db),user=Depends(writer)): return calibration.create_project(db,data,user)


@router.get('/projects/{project_id}')
def project(project_id:int,db=Depends(get_db),user=Depends(current_user)): return calibration.get_project(db,project_id)


@router.put('/projects/{project_id}')
def update_project(project_id:int,data:CalibrationProjectUpdate,db=Depends(get_db),user=Depends(writer)): return calibration.update_project(db,project_id,data,user)


@router.post('/projects/{project_id}/instruments')
def add_instrument(project_id:int,data:InstrumentInput,db=Depends(get_db),user=Depends(writer)): return calibration.add_instrument(db,project_id,data,user)


@router.delete('/projects/{project_id}/instruments/{instrument_id}')
def remove_instrument(project_id:int,instrument_id:int,db=Depends(get_db),user=Depends(writer)): return calibration.remove_instrument(db,project_id,instrument_id,user)


@router.post('/projects/{project_id}/pairs')
def add_pair(project_id:int,data:PairInput,db=Depends(get_db),user=Depends(writer)): return calibration.save_pair(db,project_id,data,user)


@router.put('/projects/{project_id}/pairs/{pair_id}')
def update_pair(project_id:int,pair_id:int,data:PairInput,db=Depends(get_db),user=Depends(writer)): return calibration.save_pair(db,project_id,data,user,pair_id)


@router.delete('/projects/{project_id}/pairs/{pair_id}')
def remove_pair(project_id:int,pair_id:int,db=Depends(get_db),user=Depends(writer)): return calibration.remove_pair(db,project_id,pair_id,user)


@router.get('/runs')
def runs(project_id:int|None=None,db=Depends(get_db),user=Depends(current_user)): return calibration.list_runs(db,project_id)


@router.post('/runs')
def create_run(data:CalibrationRunInput,db=Depends(get_db),user=Depends(writer)): return calibration.create_run(db,data,user)


@router.get('/runs/{run_id}')
def run(run_id:int,db=Depends(get_db),user=Depends(current_user)): return calibration.get_run(db,run_id)


@router.post('/runs/{run_id}/publish')
def publish(run_id:int,data:PublishInput,db=Depends(get_db),user=Depends(admin)): return calibration.publish(db,run_id,data,user)


@router.get('/runs/{run_id}/export')
def export(run_id:int,db=Depends(get_db),user=Depends(current_user)):
    content=calibration_service.export(db,run_id,user)
    return Response(content=content.encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="calibration-{run_id}.csv"'})
