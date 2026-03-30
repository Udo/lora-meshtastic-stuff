# Proxmox Backup Notes

## CT 108 (`aiworker`)

- Do not include CT `108` in snapshot-based `vzdump` jobs.
- Reason: CT `108` uses `fuse=1` and runs on-demand `sshfs` mounts under `/root/mount_ssh/`. A scheduled snapshot backup on March 30, 2026 hung at `create storage snapshot 'vzdump'`, left `lock: snapshot` behind, and caused Proxmox management calls like `pct list` and `pct status` to block when they touched CT `108`.
- Mitigation on `udo-pve1`:
  - The two `all 1` backup jobs in `/etc/pve/jobs.cfg` now contain `exclude 108`.
  - CT `108` has dedicated `mode stop` backup jobs instead:
    - `safe-108-vmbackup`: `mon 04:30`, storage `vmbackup`
    - `safe-108-k-pbs`: `sat *-1..7 15:30`, storage `k-pbs`
- If snapshot backups are ever re-enabled for CT `108`, first ensure any `sshfs` mounts are unmounted and verify that FUSE is not active inside the container.
