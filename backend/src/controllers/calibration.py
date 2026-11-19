from src.services import calibration


def list_projects(db): return calibration.list_projects(db)
def get_project(db,id): return calibration.get_project(db,id)
def create_project(db,data,user): return calibration.create_project(db,data,user)
def update_project(db,id,data,user): return calibration.update_project(db,id,data,user)
def add_instrument(db,id,data,user): return calibration.add_instrument(db,id,data,user)
def remove_instrument(db,id,instrument_id,user): return calibration.remove_instrument(db,id,instrument_id,user)
def save_pair(db,id,data,user,pair_id=None): return calibration.save_pair(db,id,data,user,pair_id)
def remove_pair(db,id,pair_id,user): return calibration.remove_pair(db,id,pair_id,user)
def create_run(db,data,user): return calibration.create_run(db,data,user)
def list_runs(db,project_id=None): return calibration.list_runs(db,project_id)
def get_run(db,id): return calibration.get_run(db,id)
def publish(db,id,data,user): return calibration.publish(db,id,data,user)
