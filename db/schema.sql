CREATE DATABASE IF NOT EXISTS se2026 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE se2026;

CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('ppl', 'pml', 'admin') NOT NULL,
    name          VARCHAR(100) NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    email         VARCHAR(100),
    phone         VARCHAR(30)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sls (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    kode_sls  VARCHAR(20)  NOT NULL UNIQUE,
    nama_sls  VARCHAR(100) NOT NULL,
    pml_id    INT NOT NULL,
    ppl_id    INT NOT NULL,
    target    INT NOT NULL DEFAULT 0,
    kode_kec  VARCHAR(3),
    nama_kec  VARCHAR(100),
    kode_desa VARCHAR(3),
    nama_desa VARCHAR(100),
    CONSTRAINT fk_sls_pml FOREIGN KEY (pml_id) REFERENCES users(id),
    CONSTRAINT fk_sls_ppl FOREIGN KEY (ppl_id) REFERENCES users(id)
) ENGINE=InnoDB;

-- PPL daily progress
CREATE TABLE IF NOT EXISTS laporan_harian (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    sls_id        INT NOT NULL,
    tanggal       DATE NOT NULL,
    jumlah_submit  INT NOT NULL DEFAULT 0,
    jumlah_draft   INT NOT NULL DEFAULT 0,
    alasan_lebih20 TEXT DEFAULT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_laporan_sls_tgl (sls_id, tanggal),
    CONSTRAINT fk_laporan_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
) ENGINE=InnoDB;

-- PML daily verification
CREATE TABLE IF NOT EXISTS verifikasi_harian (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    sls_id            INT NOT NULL,
    tanggal           DATE NOT NULL,
    jumlah_diperiksa  INT NOT NULL DEFAULT 0,
    jumlah_error      INT NOT NULL DEFAULT 0,
    jumlah_observasi  INT NOT NULL DEFAULT 0,
    status_kendala    ENUM('open','in_progress','resolved','escalated') NOT NULL DEFAULT 'open',
    tindak_lanjut_pml TEXT,
    kendala           TEXT,
    solusi_sementara  TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_verif_sls_tgl (sls_id, tanggal),
    CONSTRAINT fk_verif_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
) ENGINE=InnoDB;
