from sqlalchemy import Column,Float,ForeignKey,Integer,JSON,String,Text,UniqueConstraint
from src.config.database import Base


class CalibrationProject(Base):
    """A set of instruments calibrated against one reference instrument.

    ``revision`` is bumped by every edit of the baseline, the instrument roster or
    a pairwise deviation interval.  Run creation locks this row, so a frozen run
    always captures one coherent revision (baseline + intervals together).
    """

    __tablename__ = 'calibration_projects'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    source_filename = Column(String(120), nullable=False)
    description = Column(Text, default='')
    baseline_instrument = Column(String(100), nullable=False)
    revision = Column(Integer, nullable=False, default=0)
    created_at = Column(String(40), nullable=False)


class CalibrationInstrument(Base):
    """Instrument roster.  The baseline is always part of the roster."""

    __tablename__ = 'calibration_instruments'
    __table_args__ = (UniqueConstraint('project_id', 'name'),)
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('calibration_projects.id'), nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(String(40), nullable=False)


class CalibrationPair(Base):
    """One observed deviation interval: lower <= x_a - x_b <= upper."""

    __tablename__ = 'calibration_pairs'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('calibration_projects.id'), nullable=False)
    instrument_a = Column(String(100), nullable=False)
    instrument_b = Column(String(100), nullable=False)
    lower = Column(Float, nullable=False)
    upper = Column(Float, nullable=False)
    basis = Column(Text, default='')
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


class CalibrationRun(Base):
    """Frozen calibration snapshot plus its computed tightest ranges."""

    __tablename__ = 'calibration_runs'
    __table_args__ = (UniqueConstraint('project_id', 'idempotency_key'),)
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('calibration_projects.id'), nullable=False)
    name = Column(String(120), nullable=False)
    status = Column(String(20), default='queued', nullable=False)
    snapshot = Column(JSON, nullable=False)
    checksum = Column(String(64), nullable=False)
    snapshot_revision = Column(Integer, nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    result = Column(JSON, nullable=True)
    error = Column(Text, default='')
    created_at = Column(String(40), nullable=False)
    published_at = Column(String(40), nullable=True)
    published_by = Column(String(120), nullable=True)
    publication = Column(JSON, nullable=True)
