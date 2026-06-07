PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS keywords (
  keyword_id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  category TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(keyword)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_key TEXT NOT NULL,
  keyword_id INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  total_found INTEGER NOT NULL DEFAULT 0,
  total_saved INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  out_dir TEXT,
  raw_path TEXT,
  report_path TEXT,
  ai_output_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(keyword_id) REFERENCES keywords(keyword_id) ON DELETE SET NULL,
  UNIQUE(run_key)
);

CREATE TABLE IF NOT EXISTS authors (
  author_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'xiaohongshu',
  nickname TEXT,
  profile_url TEXT,
  avatar_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  note_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'xiaohongshu',
  author_id TEXT,
  title TEXT,
  content TEXT,
  note_url TEXT,
  publish_time TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(author_id) REFERENCES authors(author_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  author_id TEXT,
  content TEXT,
  like_count INTEGER,
  publish_time TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(note_id) REFERENCES notes(note_id) ON DELETE CASCADE,
  FOREIGN KEY(author_id) REFERENCES authors(author_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS keyword_note_map (
  keyword_id INTEGER NOT NULL,
  note_id TEXT NOT NULL,
  run_id INTEGER NOT NULL,
  rank_position INTEGER,
  search_time TEXT NOT NULL,
  PRIMARY KEY(keyword_id, note_id, run_id),
  FOREIGN KEY(keyword_id) REFERENCES keywords(keyword_id) ON DELETE CASCADE,
  FOREIGN KEY(note_id) REFERENCES notes(note_id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metrics_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id TEXT NOT NULL,
  run_id INTEGER NOT NULL,
  liked_count INTEGER,
  collected_count INTEGER,
  comment_count INTEGER,
  share_count INTEGER,
  snapshot_time TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(note_id) REFERENCES notes(note_id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE,
  UNIQUE(note_id, run_id, snapshot_time)
);

CREATE TABLE IF NOT EXISTS analysis_results (
  analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_key TEXT NOT NULL,
  run_id INTEGER,
  keyword_id INTEGER,
  analysis_type TEXT NOT NULL,
  input_summary TEXT,
  output_text TEXT,
  raw_json TEXT,
  model_name TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES crawl_runs(run_id) ON DELETE SET NULL,
  FOREIGN KEY(keyword_id) REFERENCES keywords(keyword_id) ON DELETE SET NULL,
  UNIQUE(analysis_key)
);

CREATE TABLE IF NOT EXISTS failures (
  failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
  failure_key TEXT NOT NULL,
  run_id INTEGER,
  stage TEXT NOT NULL,
  target TEXT,
  error_type TEXT,
  error_message TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(run_id) REFERENCES crawl_runs(run_id) ON DELETE SET NULL,
  UNIQUE(failure_key)
);

CREATE TABLE IF NOT EXISTS observations (
  observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  keyword_id INTEGER NOT NULL,
  observation_date TEXT NOT NULL,
  keyword TEXT NOT NULL,
  new_note_count INTEGER NOT NULL DEFAULT 0,
  new_author_count INTEGER NOT NULL DEFAULT 0,
  liked_growth INTEGER NOT NULL DEFAULT 0,
  collected_growth INTEGER NOT NULL DEFAULT 0,
  comment_growth INTEGER NOT NULL DEFAULT 0,
  top_authors_change TEXT,
  top_tags_change TEXT,
  anomalies_json TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE,
  FOREIGN KEY(keyword_id) REFERENCES keywords(keyword_id) ON DELETE CASCADE,
  UNIQUE(run_id, keyword_id)
);

CREATE TABLE IF NOT EXISTS insights (
  insight_id INTEGER PRIMARY KEY AUTOINCREMENT,
  insight_key TEXT NOT NULL,
  keyword_id INTEGER,
  keyword TEXT,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 0,
  current_trend_level TEXT,
  fastest_growing_topics TEXT,
  new_topics TEXT,
  frequent_pain_points TEXT,
  recommended_keywords TEXT,
  risk_warnings TEXT,
  one_sentence_summary TEXT,
  input_json TEXT,
  output_json TEXT,
  model_name TEXT,
  ai_status TEXT NOT NULL DEFAULT 'not_run',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(keyword_id) REFERENCES keywords(keyword_id) ON DELETE SET NULL,
  UNIQUE(insight_key)
);

CREATE TABLE IF NOT EXISTS discovered_keywords (
  discovered_id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  reason TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'candidate',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  promoted_at TEXT,
  raw_json TEXT,
  CHECK(status IN ('candidate', 'watching', 'rejected', 'promoted')),
  UNIQUE(keyword)
);

CREATE INDEX IF NOT EXISTS idx_keywords_keyword_id ON keywords(keyword_id);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_keyword_id ON crawl_runs(keyword_id);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_run_id ON crawl_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_started_at ON crawl_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_notes_note_id ON notes(note_id);
CREATE INDEX IF NOT EXISTS idx_notes_publish_time ON notes(publish_time);
CREATE INDEX IF NOT EXISTS idx_notes_author_id ON notes(author_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_authors_profile_url_unique
  ON authors(platform, profile_url)
  WHERE profile_url IS NOT NULL AND profile_url <> '';
CREATE INDEX IF NOT EXISTS idx_comments_note_id ON comments(note_id);
CREATE INDEX IF NOT EXISTS idx_keyword_note_map_keyword_id ON keyword_note_map(keyword_id);
CREATE INDEX IF NOT EXISTS idx_keyword_note_map_run_id ON keyword_note_map(run_id);
CREATE INDEX IF NOT EXISTS idx_keyword_note_map_note_id ON keyword_note_map(note_id);
CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_note_id ON metrics_snapshots(note_id);
CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_run_id ON metrics_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_snapshot_time ON metrics_snapshots(snapshot_time);
CREATE INDEX IF NOT EXISTS idx_analysis_results_run_id ON analysis_results(run_id);
CREATE INDEX IF NOT EXISTS idx_failures_run_id ON failures(run_id);
CREATE INDEX IF NOT EXISTS idx_failures_resolved ON failures(resolved);
CREATE INDEX IF NOT EXISTS idx_observations_run_id ON observations(run_id);
CREATE INDEX IF NOT EXISTS idx_observations_keyword_id ON observations(keyword_id);
CREATE INDEX IF NOT EXISTS idx_observations_observation_date ON observations(observation_date);
CREATE INDEX IF NOT EXISTS idx_insights_keyword_id ON insights(keyword_id);
CREATE INDEX IF NOT EXISTS idx_insights_period_end ON insights(period_end);
CREATE INDEX IF NOT EXISTS idx_insights_ai_status ON insights(ai_status);
CREATE INDEX IF NOT EXISTS idx_discovered_keywords_keyword ON discovered_keywords(keyword);
CREATE INDEX IF NOT EXISTS idx_discovered_keywords_status ON discovered_keywords(status);
CREATE INDEX IF NOT EXISTS idx_discovered_keywords_last_seen_at ON discovered_keywords(last_seen_at);
