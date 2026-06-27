CREATE TABLE IF NOT EXISTS anomali (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  sls_id        INT NOT NULL,
  assignment_id CHAR(36) NOT NULL,
  nama          VARCHAR(255) DEFAULT '',
  jenis         VARCHAR(100) DEFAULT '',
  rule_key      VARCHAR(20) NOT NULL,
  rule_msg      TEXT,
  rule_type     TINYINT DEFAULT 1,
  synced_at     DATETIME,
  UNIQUE KEY uk_assignment_rule (assignment_id, rule_key),
  KEY idx_sls_id (sls_id),
  CONSTRAINT fk_anomali_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
);
