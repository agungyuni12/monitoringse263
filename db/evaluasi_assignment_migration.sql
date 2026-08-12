-- Migration: tabel evaluasi_assignment — fitur "Evaluasi & Catatan Perbaikan
-- Assignment Organik". Petugas organik mencatat rincian kesalahan kuesioner
-- per assignment hasil pencacahan FASIH-SM (satu assignment bisa punya banyak
-- catatan perbaikan, satu baris per rincian). PML/PPL penanggung jawab SLS
-- terkait menandai status jadi 'resolved' beserta catatan tindak lanjutnya.
CREATE TABLE IF NOT EXISTS evaluasi_assignment (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    organik_id            INT NOT NULL,
    sls_id                INT NOT NULL,
    assignment_id         VARCHAR(100) NOT NULL,
    rincian_kuesioner     VARCHAR(150) NOT NULL,
    jenis_kesalahan       ENUM('konflik_isian','nilai_ekstrem','tidak_konsisten','lainnya') NOT NULL,
    rekomendasi           TEXT NOT NULL,
    status                ENUM('open','resolved') NOT NULL DEFAULT 'open',
    catatan_tindak_lanjut TEXT,
    tindak_lanjut_by      INT,
    tindak_lanjut_at      DATETIME,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_ea_assignment (assignment_id),
    KEY idx_ea_sls (sls_id),
    KEY idx_ea_organik (organik_id),
    KEY idx_ea_status (status),
    CONSTRAINT fk_ea_organik FOREIGN KEY (organik_id) REFERENCES users(id),
    CONSTRAINT fk_ea_sls FOREIGN KEY (sls_id) REFERENCES sls(id),
    CONSTRAINT fk_ea_tindak_lanjut_by FOREIGN KEY (tindak_lanjut_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SELECT 'Migration evaluasi_assignment selesai.' AS status;
