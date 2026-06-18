-- Fix alokasi PML Manggalewa:
-- Irman Fitriani dan M Iksan seharusnya di bawah PML MUSLIMIN AKBAR ARS (id=58),
-- bukan Wira Nurmayadi dan Syahril Sidik.
UPDATE sls
SET pml_id = (SELECT id FROM users WHERE name = 'MUSLIMIN AKBAR ARS' AND role = 'pml')
WHERE ppl_id IN (
    SELECT id FROM users WHERE name IN ('Irman Fitriani', 'M Iksan') AND role = 'ppl'
) AND nama_kec = 'MANGGALEWA';
