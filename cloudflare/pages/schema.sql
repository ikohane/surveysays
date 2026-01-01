-- SurveySays staging schema (D1)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT NOT NULL UNIQUE,
  layout_config_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

CREATE TABLE IF NOT EXISTS invitations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  email TEXT,
  first_name TEXT,
  last_name TEXT,
  opened_at TEXT,
  questionnaire_version INTEGER,
  questionnaire_hash TEXT,
  questionnaire_json TEXT NOT NULL,
  metadata_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS invitations_campaign_id_idx ON invitations(campaign_id);
CREATE INDEX IF NOT EXISTS invitations_hash_idx ON invitations(questionnaire_hash);

CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  token TEXT NOT NULL,
  answers_json TEXT NOT NULL,
  submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
  UNIQUE (campaign_id, token),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS submissions_campaign_id_idx ON submissions(campaign_id);


