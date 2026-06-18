-- Jalankan sekali untuk menambah fitur organik
-- Password default semua organik: 12345

ALTER TABLE users MODIFY COLUMN role ENUM('ppl','pml','admin','organik') NOT NULL;

CREATE TABLE IF NOT EXISTS laporan_organik (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organik_id      INT NOT NULL,
    sls_id          INT NOT NULL,
    tanggal         DATE NOT NULL,
    jumlah_diawasi  INT NOT NULL DEFAULT 0,
    kendala         TEXT,
    solusi          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_org_sls_tgl (organik_id, sls_id, tanggal),
    CONSTRAINT fk_lo_organik FOREIGN KEY (organik_id) REFERENCES users(id),
    CONSTRAINT fk_lo_sls     FOREIGN KEY (sls_id)     REFERENCES sls(id)
) ENGINE=InnoDB;

-- Password hash untuk "12345"
-- $2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq

INSERT INTO users (username, password_hash, role, name, email) VALUES
('197802232000121002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Ahwan Hadi, S.ST., M.Ak','ahwan@bps.go.id'),
('197009241994012001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Dewi Utari','dewiutari@bps.go.id'),
('197204041998031005','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Abdul Farid, SE','abdul.farid@bps.go.id'),
('197312312007011009','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Ismail, SE','ismail4@bps.go.id'),
('197508021996032002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Rita, S.Sos','rita3@bps.go.id'),
('197611092007011009','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Iksan','iksan2@bps.go.id'),
('197712022009111001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Suwardin, SE','suwardin@bps.go.id'),
('198107192007011001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Muhlis','muhlis3@bps.go.id'),
('198209042009011007','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Muhammad Ali, SE','muhammad.ali@bps.go.id'),
('198305132011011009','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Abdul Mufakhir, S.Psi., M.Ak','fahir@bps.go.id'),
('198412142010031001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Bonny Leo Wattimena, SE','bonnyleo@bps.go.id'),
('198507052008012006','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Baiq Ema S. Yulyanti, S.Pd','emasyulyanti@bps.go.id'),
('199310202018021001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Octavianus Yakobus Nggoe, SST','octavianus.yakobus@bps.go.id'),
('199403112017012001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Mardiati, SST','mardiati2@bps.go.id'),
('199411122023212029','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Hernanda Novitasari, SST','hernandanovi-pppk@bps.go.id'),
('199504282017012001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Anggraeni Mutyasari, S.ST','anggraeni.mutyasari@bps.go.id'),
('199507302019011002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Aditya Dwi Yulianto, S.Tr.Stat','aditya.dwi@bps.go.id'),
('199511142017012001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Nurfitriati, SST','nurfitriati@bps.go.id'),
('199705162022011001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Indra Dwi Wicaksono, S.Tr.Stat.','indradwi@bps.go.id'),
('199806032021041001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Agung Yuniarta Sosiawan, S.Tr.Stat.','agung.yuniarta@bps.go.id'),
('199809032022012002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Emalia Septiani Hirma, S.Tr.Stat.','emaliahirma@bps.go.id'),
('199811082021041001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Wayan Delva Budi Darmika, S.Stat','delva.budi@bps.go.id'),
('200004162022011002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','M. Chumaidi Rahman, A.Md.Stat.','chumaidi.rahman@bps.go.id'),
('200006042023022002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Inas Zaizafun Satira, S.Tr.Stat','inas.satira@bps.go.id'),
('200008282023022003','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Salsabila Puspita Wahyuni, A.Md.Stat','salsapuspita@bps.go.id'),
('200010272023021004','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Muhammad Rianbarenito Gunawan, A.Md.Stat','rianbarenito@bps.go.id'),
('200012062022011001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Erwin Muspian Hakiki, A.Md.Stat.','erwin.muspian@bps.go.id'),
('200106172023102001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Nabila Putri Lestari, S.Tr.Stat','nabila.lestari@bps.go.id'),
('200105312024121003','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Maulana Rizki Rahmat, A.Md.Stat.','maulana.rizki@bps.go.id'),
('200305132024121002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Miftahur Rahim, A.Md.Stat.','miftahur.rahim@bps.go.id'),
('200303242024121004','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Putra Bimo As''ari, A.Md.Stat.','bimo.asari@bps.go.id'),
('200203012024121004','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Rizky Wahyuda Manik, S.Tr.Stat.','wahyuda.manik@bps.go.id')
ON DUPLICATE KEY UPDATE role='organik';

-- 8 organik tambahan (PPPK dan ASN yang belum masuk di atas)
INSERT INTO users (username, password_hash, role, name, email) VALUES
('197206112025211014','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Syarifuddin','syarifu-pppk@bps.go.id'),
('197407252025211021','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Ikhlas','ikhlas-pppk@bps.go.id'),
('198401032025211047','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Sukriawan','sukriawan-pppk@bps.go.id'),
('198809172025211049','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Iswahyudin','iswahyudin-pppk@bps.go.id'),
('199412112017012002','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Nurwadarahmah, SST','nurwadarahmah@bps.go.id'),
('200003282025211012','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Rijal','rijal-pppk@bps.go.id'),
('200306222026031001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Nicholas Rahardian Kurnia Sandy, S.Tr.Stat.','nicholas.rahardian@bps.go.id'),
('200308092026032001','$2a$10$4QIOLca3nnUF8ki/4e9ZQ.5kLvjmUztkOYdh5uUKwLWsAL1PqZdXq','organik','Tiara Putri Setia Puspita, S.Tr.Stat.','tiara.puspita@bps.go.id')
ON DUPLICATE KEY UPDATE role='organik';
