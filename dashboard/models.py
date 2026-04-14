import json
from datetime import date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ── Helper for JSON list columns ──────────────────────────────────────────────

class JsonList(db.TypeDecorator):
    """Stores a Python list as a JSON text string. Works with SQLite and Postgres."""
    impl = db.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return '[]'
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return []
        return json.loads(value)


# ── Models ────────────────────────────────────────────────────────────────────

class Lead(db.Model):
    __tablename__ = 'leads'

    id                 = db.Column(db.Text, primary_key=True)
    name               = db.Column(db.Text)
    company            = db.Column(db.Text)
    phone              = db.Column(db.Text)
    email              = db.Column(db.Text)
    status             = db.Column(db.Text)
    lead_source        = db.Column(db.Text)
    lead_age_days      = db.Column(db.Integer)
    last_activity_date = db.Column(db.Text)
    last_activity_type = db.Column(db.Text)
    next_task          = db.Column(db.Text)
    next_task_due      = db.Column(db.Text)
    call_attempts      = db.Column(db.Integer, default=0)
    city               = db.Column(db.Text)
    state              = db.Column(db.Text)
    notes_snippet      = db.Column(db.Text)
    last_call_notes    = db.Column(db.Text)
    activity_summary   = db.Column(db.Text)
    next_agreed_step   = db.Column(db.Text)
    open_tasks         = db.Column(JsonList)
    is_recycled        = db.Column(db.Boolean, default=False)
    manually_added     = db.Column(db.Boolean, default=False)
    extracted_at       = db.Column(db.Text)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Opportunity(db.Model):
    __tablename__ = 'opportunities'

    id                 = db.Column(db.Text, primary_key=True)
    name               = db.Column(db.Text)
    account_name       = db.Column(db.Text)
    contact_name       = db.Column(db.Text)
    phone              = db.Column(db.Text)
    stage              = db.Column(db.Text)
    amount             = db.Column(db.Numeric, default=0)
    close_date         = db.Column(db.Text)
    last_activity_date = db.Column(db.Text)
    last_activity_type = db.Column(db.Text)
    next_step          = db.Column(db.Text)
    next_task_due      = db.Column(db.Text)
    days_in_stage      = db.Column(db.Integer, default=0)
    probability        = db.Column(db.Numeric, default=0)
    notes_snippet      = db.Column(db.Text)
    last_call_notes    = db.Column(db.Text)
    activity_summary   = db.Column(db.Text)
    next_agreed_step   = db.Column(db.Text)
    open_tasks         = db.Column(JsonList)
    manually_added     = db.Column(db.Boolean, default=False)
    extracted_at       = db.Column(db.Text)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d['type'] = 'opportunity'
        d['amount'] = float(d['amount']) if d['amount'] else 0
        d['probability'] = float(d['probability']) if d['probability'] else 0
        return d


class Callback(db.Model):
    __tablename__ = 'callbacks'

    id              = db.Column(db.Text, primary_key=True)
    name            = db.Column(db.Text)
    contact_name    = db.Column(db.Text)
    phone           = db.Column(db.Text)
    sf_url          = db.Column(db.Text)
    task_due        = db.Column(db.Text)
    notes           = db.Column(db.Text)
    do_not_call     = db.Column(db.Boolean, default=False)
    added_date      = db.Column(db.Text)
    last_call_date  = db.Column(db.Text)
    last_call_notes = db.Column(db.Text)
    call_attempts   = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class KpiLog(db.Model):
    __tablename__ = 'kpi_log'

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date         = db.Column(db.Text, unique=True, nullable=False)
    dials        = db.Column(db.Integer, default=0)
    connects     = db.Column(db.Integer, default=0)
    voicemails   = db.Column(db.Integer, default=0)
    demos_set    = db.Column(db.Integer, default=0)
    applications = db.Column(db.Integer, default=0)
    closes       = db.Column(db.Integer, default=0)
    logged_at    = db.Column(db.Text)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class RecycledLead(db.Model):
    __tablename__ = 'recycled_leads'

    id                 = db.Column(db.Text, primary_key=True)
    name               = db.Column(db.Text)
    company            = db.Column(db.Text)
    phone              = db.Column(db.Text)
    email              = db.Column(db.Text)
    status             = db.Column(db.Text)
    lead_source        = db.Column(db.Text)
    lead_created       = db.Column(db.Text)
    last_activity_date = db.Column(db.Text)
    is_converted       = db.Column(db.Boolean, default=False)
    converted_opp_id   = db.Column(db.Text)
    category           = db.Column(db.Text)  # no_contact | no_activity | had_conversation
    attempt_count      = db.Column(db.Integer, default=0)
    last_attempt       = db.Column(db.Text)
    attempt_summary    = db.Column(db.Text)
    extracted_at       = db.Column(db.Text)
    color              = db.Column(db.Text)  # yellow | red | blue | light_green | dark_green | purple
    timezone           = db.Column(db.Text)  # ET | CT | MT | PT | AK | HI

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class LeadColor(db.Model):
    """Persists user-assigned colors across re-scans, keyed by Salesforce ID."""
    __tablename__ = 'lead_colors'

    sf_id  = db.Column(db.Text, primary_key=True)
    color  = db.Column(db.Text, nullable=False)

    def to_dict(self):
        return {'sf_id': self.sf_id, 'color': self.color}


class RefreshLog(db.Model):
    __tablename__ = 'refresh_log'

    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    refresh_type     = db.Column(db.Text)   # 'salesforce' | 'recycled'
    refreshed_at     = db.Column(db.Text)
    lead_count       = db.Column(db.Integer)
    opp_count        = db.Column(db.Integer)
    detail_pass_leads = db.Column(db.Integer)
    detail_pass_opps  = db.Column(db.Integer)
    total_leads      = db.Column(db.Integer)
    no_activity      = db.Column(db.Integer)
    no_contact       = db.Column(db.Integer)
    had_conversation = db.Column(db.Integer)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class BossMetrics(db.Model):
    """Stores lead funnel metrics: leads received → converted → closed won."""
    __tablename__ = 'boss_metrics'

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    refreshed_at = db.Column(db.Text)
    mtd          = db.Column(JsonList)   # dict stored as JSON
    ytd          = db.Column(JsonList)   # dict stored as JSON
    monthly      = db.Column(JsonList)   # list of monthly dicts

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class TeamMetrics(db.Model):
    """Stores MTD team leaderboard data pulled from Salesforce via SF CLI."""
    __tablename__ = 'team_metrics'

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    refreshed_at = db.Column(db.Text)
    month        = db.Column(db.Text)
    month_start  = db.Column(db.Text)
    reps         = db.Column(JsonList)   # list of rep dicts

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class SFTaskData(db.Model):
    """Stores today's completed + upcoming scheduled tasks pulled from Salesforce."""
    __tablename__ = 'sf_task_data'

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    refreshed_at = db.Column(db.Text)
    date         = db.Column(db.Text)
    completed    = db.Column(JsonList)   # tasks completed today
    scheduled    = db.Column(JsonList)   # open tasks due today or later
    daily_count  = db.Column(db.Integer, default=0)  # tasks + notes completed today
    weekly_count = db.Column(db.Integer, default=0)  # tasks + notes completed Mon–today
    week_start   = db.Column(db.Text)                # ISO date of Monday

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class SkippedToday(db.Model):
    __tablename__ = 'skipped_today'

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_id  = db.Column(db.Text, nullable=False)
    skip_date  = db.Column(db.Text, nullable=False)

    __table_args__ = (db.UniqueConstraint('record_id', 'skip_date'),)


class EmailTemplate(db.Model):
    """User-defined email templates for auto-drafting. Slots 1–3."""
    __tablename__ = 'email_templates'

    id      = db.Column(db.Integer, primary_key=True, autoincrement=True)
    slot    = db.Column(db.Integer, nullable=False, unique=True)  # 1, 2, or 3
    name    = db.Column(db.Text, default='')
    subject = db.Column(db.Text, default='')
    body    = db.Column(db.Text, default='')

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class UserNotes(db.Model):
    """Persistent notes pad — single row, keyed by note_key."""
    __tablename__ = 'user_notes'

    note_key  = db.Column(db.Text, primary_key=True)
    content   = db.Column(db.Text, default='')
    updated_at = db.Column(db.Text)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class LeadEmailQueue(db.Model):
    """Tracks which leads/opps are queued for email drafting, and which template."""
    __tablename__ = 'lead_email_queue'

    sf_id    = db.Column(db.Text, primary_key=True)
    slot     = db.Column(db.Integer, nullable=False)   # 1, 2, or 3
    queued_at = db.Column(db.Text)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
