import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models import run,review,audit,calibration
from src.models.calibration import CalibrationProject,CalibrationInstrument,CalibrationPair
from src.utils.security import hash_password
from src.utils.serializers import now

def seed():
    Base.metadata.create_all(engine)
    rng=np.random.default_rng(42)
    i=DOMAIN['index']
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            rows=[]
            if i==0:
                for n in range(120): rows.append({'moisture':round(float(np.clip(rng.normal(5.2+k*.7,.7),0,100)),3),'extract':round(float(np.clip(rng.normal(34+k*2,2.4),0,100)),3)})
            elif i==1:
                rows=[{'group':'A','success':87+k*2,'failure':13-k*2},{'group':'B','success':96-k,'failure':4+k}]
            elif i==2:
                for n in range(24):
                    b=float(rng.uniform(3,6));rows.append({'before':round(b,3),'after':round(max(.1,b+rng.normal(-.45+k*.3,.5)),3)})
            elif i==3:
                for n in range(90):
                    x=round(float(rng.uniform(4,28)),1);rows.append({'porosity':x,'absorption':round(max(0,x*(.18+k*.08)+rng.normal(0,1.5)),2)})
            else: rows=[{'p0':.08+k*.02,'p1':.1+k*.025,'cost_per_contact':.12+k*.04,'budget':3000+k*500}]
            for n,values in enumerate(rows):
                key=f'DEMO-{k+1}-{n+1:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,values=values))
        seed_calibration(db)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation,CalibrationProject,CalibrationPair)}
        print(json.dumps(counts));return counts


CALIBRATION_DEMO=[
    {'name':'吸水率仪器比对 2026-11',
     'source_filename':'calibration-202611.csv',
     'baseline':'M0',
     'instruments':['M0','M1','M2','M3','M4'],
     'pairs':[
        ('M1','M0',-0.62,0.41,'标准样复测 3 次 / 证书 A-11'),
        ('M2','M1',-0.24,0.30,'标准样复测 3 次 / 证书 A-12'),
        ('M3','M2',0.10,0.48,'同批细砂岩复测 / 批次 B-2'),
        ('M2','M0',-0.95,0.80,'交叉验证 / 2026-10 汇总'),
        ('M4','M3',-0.18,0.22,'备用机比对 / 证书 A-19'),
     ]},
    {'name':'备用机比对（尚未接入基准）',
     'source_filename':'standby-202611.csv',
     'baseline':'S0',
     'instruments':['S0','S1','S2'],
     'pairs':[
        ('S1','S2',-0.30,0.25,'两台备用机互比，未与主基准连通'),
     ]},
]

def seed_calibration(db):
    """Idempotent demo: natural key on project name, rows only if absent."""
    stamp=now()
    projects=[]
    for demo in CALIBRATION_DEMO:
        project=db.scalar(select(CalibrationProject).where(CalibrationProject.name==demo['name']))
        if project:
            projects.append(project);continue
        project=CalibrationProject(name=demo['name'],source_filename=demo['source_filename'],description='合成吸水率仪器比对记录 / 仅用于方法与流程演示',baseline_instrument=demo['baseline'],revision=0,created_at=stamp)
        db.add(project);db.flush()
        for name in demo['instruments']:
            db.add(CalibrationInstrument(project_id=project.id,name=name,created_at=stamp))
        for a,b,lo,hi,basis in demo['pairs']:
            db.add(CalibrationPair(project_id=project.id,instrument_a=a,instrument_b=b,lower=lo,upper=hi,basis=basis,created_at=stamp,updated_at=stamp))
        # baseline is part of the project definition; each added instrument or
        # interval bumps the revision once, matching the API's edit accounting.
        project.revision=max(0,len(demo['instruments'])-1)+len(demo['pairs'])
        projects.append(project)
    return projects
if __name__=='__main__': seed()
