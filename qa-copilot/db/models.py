import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Enum,
    ForeignKey, Text, Boolean, JSON, Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class SessionStatus(str, enum.Enum):
    pending   = "pending"
    running   = "running"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed    = "failed"


class Severity(str, enum.Enum):
    low      = "low"
    medium   = "medium"
    high     = "high"
    critical = "critical"


class Session(Base):
    __tablename__ = "sessions"

    id            = Column(String, primary_key=True, default=_uuid)
    url           = Column(String, nullable=False)
    status        = Column(Enum(SessionStatus), default=SessionStatus.pending, nullable=False)
    config        = Column(JSON, default=dict)          # stores final report after completion
    created_at    = Column(DateTime, default=datetime.utcnow)
    started_at    = Column(DateTime, nullable=True)
    completed_at  = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Token usage tracking
    total_input_tokens  = Column(Integer, default=0)
    total_output_tokens = Column(Integer, default=0)

    bugs           = relationship("Bug",          back_populates="session", cascade="all, delete-orphan")
    page_nodes     = relationship("PageNode",     back_populates="session", cascade="all, delete-orphan")
    test_runs      = relationship("TestRun",      back_populates="session", cascade="all, delete-orphan")
    console_errors = relationship("ConsoleError", back_populates="session", cascade="all, delete-orphan")
    network_failures = relationship("NetworkFailure", back_populates="session", cascade="all, delete-orphan")
    events         = relationship("SessionEvent", back_populates="session", cascade="all, delete-orphan")


class TestRun(Base):
    """One test run per persona per session."""
    __tablename__ = "test_runs"

    id           = Column(String, primary_key=True, default=_uuid)
    session_id   = Column(String, ForeignKey("sessions.id"), nullable=False)
    persona_name = Column(String, nullable=False)
    persona_style= Column(String, nullable=False)
    status       = Column(String, default="pending")
    actions_taken= Column(Integer, default=0)
    pages_visited= Column(Integer, default=0)
    bugs_found   = Column(Integer, default=0)
    started_at   = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    session = relationship("Session", back_populates="test_runs")
    bugs    = relationship("Bug", back_populates="test_run")


class Bug(Base):
    __tablename__ = "bugs"

    id                = Column(String, primary_key=True, default=_uuid)
    session_id        = Column(String, ForeignKey("sessions.id"), nullable=False)
    test_run_id       = Column(String, ForeignKey("test_runs.id"), nullable=True)
    title             = Column(String, nullable=False)
    severity          = Column(Enum(Severity), nullable=False)
    description       = Column(Text, nullable=False)
    reproduction_steps= Column(JSON, default=list)
    expected_behavior = Column(Text, nullable=False)
    actual_behavior   = Column(Text, nullable=False)
    screenshot_path   = Column(String, nullable=True)
    url_at_error      = Column(String, nullable=True)
    element_selector  = Column(String, nullable=True)
    error_type        = Column(String, nullable=True)
    persona_name      = Column(String, nullable=True)
    # Vision analysis flag
    detected_by_vision= Column(Boolean, default=False)
    created_at        = Column(DateTime, default=datetime.utcnow)

    session  = relationship("Session",  back_populates="bugs")
    test_run = relationship("TestRun",  back_populates="bugs")

    __table_args__ = (
        Index("ix_bugs_session_severity", "session_id", "severity"),
    )


class PageNode(Base):
    """Discovered pages forming the navigation graph."""
    __tablename__ = "page_nodes"

    id                   = Column(String, primary_key=True, default=_uuid)
    session_id           = Column(String, ForeignKey("sessions.id"), nullable=False)
    url                  = Column(String, nullable=False)
    title                = Column(String, nullable=True)
    page_type            = Column(String, nullable=True)
    interactive_elements = Column(JSON, default=list)
    outgoing_links       = Column(JSON, default=list)
    visited_at           = Column(DateTime, default=datetime.utcnow)
    load_time_ms         = Column(Float, nullable=True)
    has_errors           = Column(Boolean, default=False)
    visual_quality       = Column(String, nullable=True)  # good | degraded | broken

    session = relationship("Session", back_populates="page_nodes")


class ConsoleError(Base):
    __tablename__ = "console_errors"

    id          = Column(String, primary_key=True, default=_uuid)
    session_id  = Column(String, ForeignKey("sessions.id"), nullable=False)
    test_run_id = Column(String, ForeignKey("test_runs.id"), nullable=True)
    error_type  = Column(String, nullable=False)
    message     = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    url         = Column(String, nullable=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="console_errors")


class NetworkFailure(Base):
    __tablename__ = "network_failures"

    id          = Column(String, primary_key=True, default=_uuid)
    session_id  = Column(String, ForeignKey("sessions.id"), nullable=False)
    test_run_id = Column(String, ForeignKey("test_runs.id"), nullable=True)
    request_url = Column(String, nullable=False)
    method      = Column(String, nullable=True)
    status_code = Column(Integer, nullable=True)
    error_text  = Column(Text, nullable=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="network_failures")


class SessionEvent(Base):
    """
    Persistent activity log — every agent action recorded here.
    Enables replaying the live feed after page refresh.
    """
    __tablename__ = "session_events"

    id         = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    event_type = Column(String, nullable=False)
    persona    = Column(String, nullable=True)
    message    = Column(Text, nullable=False, default="")
    data       = Column(JSON, default=dict)
    timestamp  = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="events")

    __table_args__ = (
        Index("ix_session_events_session_ts", "session_id", "timestamp"),
    )


# ---------------------------------------------------------------------------
# n8n-inspired automation layer
# ---------------------------------------------------------------------------

class Template(Base):
    """Saved test configurations — launch with one click (n8n: workflow templates)."""
    __tablename__ = "templates"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    url         = Column(String, nullable=False)
    config      = Column(JSON, default=dict)   # max_depth, personas, etc.
    tags        = Column(JSON, default=list)
    use_count   = Column(Integer, default=0)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Schedule(Base):
    """Cron-triggered QA sessions (n8n: scheduled triggers)."""
    __tablename__ = "schedules"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False)
    url         = Column(String, nullable=False)
    cron_expr   = Column(String, nullable=False)   # e.g. "0 */6 * * *"
    config      = Column(JSON, default=dict)
    enabled     = Column(Boolean, default=True)
    webhook_token = Column(String, nullable=True)  # for inbound webhook trigger
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_session_id = Column(String, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_schedules_enabled", "enabled"),
    )


class Environment(Base):
    """Named test environments (dev / staging / prod) with base URL + vars."""
    __tablename__ = "environments"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False, unique=True)
    base_url    = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    variables   = Column(JSON, default=dict)   # key→value
    is_default  = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Variable(Base):
    """Global reusable key-value pairs (n8n: global variables)."""
    __tablename__ = "variables"

    id          = Column(String, primary_key=True, default=_uuid)
    key         = Column(String, nullable=False, unique=True)
    value       = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Credential(Base):
    """Encrypted secrets for authenticated testing (n8n: credentials store)."""
    __tablename__ = "credentials"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False)
    cred_type   = Column(String, nullable=False)  # basic | bearer | cookie | api_key
    data        = Column(JSON, nullable=False)     # encrypted JSON blob
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class NotificationTarget(Base):
    """Outbound alerts on session complete or bug found (n8n: alerting)."""
    __tablename__ = "notification_targets"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False)
    target_type = Column(String, nullable=False)  # webhook | slack | discord | email
    url         = Column(String, nullable=True)    # webhook / Slack URL
    email       = Column(String, nullable=True)
    events      = Column(JSON, default=list)       # ["session_completed","bug_critical"]
    min_severity= Column(String, default="medium") # low|medium|high|critical
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Integration(Base):
    """External bug-filing integrations (GitHub Issues, Jira, Linear)."""
    __tablename__ = "integrations"

    id          = Column(String, primary_key=True, default=_uuid)
    name        = Column(String, nullable=False)
    integ_type  = Column(String, nullable=False)  # github | jira | linear
    config      = Column(JSON, nullable=False)     # repo, project, token, etc.
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
