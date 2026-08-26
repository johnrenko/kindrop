import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, FileKey, Folder, HardDrive, History, LogOut, Mail, PlugZap, Radio, Save, Trash2 } from "lucide-react";

import { api, formatBytes } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";
import type { DriveFolder, Settings, SshHostKey } from "../types";

type Crumb = DriveFolder;

export function SettingsPage() {
  const client = useQueryClient();
  const setup = useQuery({ queryKey: queryKeys.setup, queryFn: api.setup });
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.settings });
  const sshStatus = useQuery({ queryKey: queryKeys.sshStatus, queryFn: api.sshStatus });
  const profiles = useQuery({ queryKey: queryKeys.profiles, queryFn: api.profiles });
  const [draft, setDraft] = useState<Settings | null>(null);
  const [hostKey, setHostKey] = useState<SshHostKey | null>(null);
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ id: "root", name: "My Drive" }]);
  const current = crumbs.at(-1)!;
  const folders = useQuery({
    queryKey: ["folders", current.id],
    queryFn: () => api.folders(current.id),
    enabled: Boolean(setup.data?.google_connected),
  });
  useEffect(() => {
    if (settingsQuery.data && !draft) setDraft(settingsQuery.data);
  }, [draft, settingsQuery.data]);

  const refreshSetup = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.setup }),
      client.invalidateQueries({ queryKey: queryKeys.settings }),
    ]);
  };
  const save = useMutation({ mutationFn: api.saveSettings, onSuccess: refreshSetup });
  const sshTest = useMutation({
    mutationFn: api.testSsh,
    onSuccess: (status) => client.setQueryData(queryKeys.sshStatus, status),
  });
  const inspectHostKey = useMutation({
    mutationFn: api.inspectSshHostKey,
    onSuccess: setHostKey,
  });
  const trustHostKey = useMutation({
    mutationFn: api.trustSshHostKey,
    onSuccess: async () => {
      setHostKey(null);
      const status = await api.testSsh();
      client.setQueryData(queryKeys.sshStatus, status);
    },
  });
  const upload = useMutation({ mutationFn: api.uploadGoogleClient, onSuccess: refreshSetup });
  const connect = useMutation({
    mutationFn: api.oauthStart,
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
  });
  const disconnect = useMutation({ mutationFn: api.disconnectGoogle, onSuccess: refreshSetup });
  const purge = useMutation({ mutationFn: api.purgeCache });
  const clearHistory = useMutation({
    mutationFn: api.clearHistory,
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.jobs }),
        client.invalidateQueries({ queryKey: queryKeys.scans }),
        client.invalidateQueries({ queryKey: queryKeys.candidates }),
      ]);
    },
  });

  const chooseFolder = (folder: Crumb) => {
    if (!draft) return;
    const next = { ...draft, source_folder_id: folder.id, source_folder_name: folder.name };
    setDraft(next);
    save.mutate(next);
  };
  const currentSshStatus = sshTest.data ?? sshStatus.data;

  return (
    <div className="page settings-page">
      <header className="page-header page-header--compact">
        <span className="eyebrow">Settings</span>
        <h1>Prepare the route<br /><em>once, carefully.</em></h1>
        <p className="lead">Credentials stay encrypted on this machine. Kindrop asks Drive for read-only access.</p>
      </header>

      <div className="settings-sections">
        <section className="settings-section">
          <div className="settings-section__number">01</div>
          <div className="settings-section__intro">
            <span className="eyebrow">Google connection</span>
            <h2>Open the library</h2>
            <p>Upload the OAuth client JSON from your private Google Cloud project, then connect the account that owns the Drive library.</p>
          </div>
          <div className="settings-section__body credential-panel">
            <div className="connection-line">
              <div><span>OAuth client</span><strong>{setup.data?.client_configured ? "Stored and encrypted" : "Not uploaded"}</strong></div>
              <StatusBadge status={setup.data?.client_configured ? "verified" : "action_required"} />
            </div>
            <label className="file-drop">
              <FileKey size={24} />
              <span><strong>Choose OAuth client JSON</strong><small>The file is encrypted before it reaches the database.</small></span>
              <input
                type="file"
                accept="application/json,.json"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  try { upload.mutate(JSON.parse(await file.text())); }
                  catch { event.target.setCustomValidity("Choose a valid Google OAuth client JSON file"); event.target.reportValidity(); }
                }}
              />
            </label>
            <div className="connection-line">
              <div><span>Google account</span><strong>{setup.data?.google_email ?? "Not connected"}</strong></div>
              {setup.data?.google_connected ? (
                <button className="button button--secondary" onClick={() => disconnect.mutate()}><LogOut size={16} /> Disconnect</button>
              ) : (
                <button className="button button--primary" disabled={!setup.data?.client_configured || connect.isPending} onClick={() => connect.mutate()}>
                  <PlugZap size={17} /> Connect Google
                </button>
              )}
            </div>
            {(upload.error || connect.error || disconnect.error) && <p className="form-error">{(upload.error || connect.error || disconnect.error)?.message}</p>}
          </div>
        </section>

        <section className="settings-section">
          <div className="settings-section__number">02</div>
          <div className="settings-section__intro">
            <span className="eyebrow">Source Folder</span>
            <h2>Choose one branch</h2>
            <p>Kindrop scans this folder recursively. It never moves, renames or deletes Drive files.</p>
          </div>
          <div className="settings-section__body folder-browser">
            <div className="breadcrumbs" aria-label="Drive folder path">
              {crumbs.map((crumb, index) => (
                <span key={crumb.id}>
                  <button onClick={() => setCrumbs(crumbs.slice(0, index + 1))}>{crumb.name}</button>
                  {index < crumbs.length - 1 && <ChevronRight size={14} />}
                </span>
              ))}
            </div>
            {!setup.data?.google_connected ? (
              <p className="notice">Connect Google to browse My Drive.</p>
            ) : folders.isLoading ? (
              <div className="folder-skeleton" aria-label="Loading Drive folders" />
            ) : (
              <ul className="folder-list">
                {folders.data?.folders.map((folder) => (
                  <li key={folder.id}>
                    <button onClick={() => setCrumbs([...crumbs, folder])}><Folder size={18} fill="currentColor" /> {folder.name}<ChevronRight size={16} /></button>
                  </li>
                ))}
                {!folders.data?.folders.length && <li className="quiet-copy">No child folders here.</li>}
              </ul>
            )}
            <div className="folder-choice">
              <div><span>Current choice</span><strong>{draft?.source_folder_name ?? "None"}</strong></div>
              <button className="button button--ink" disabled={!setup.data?.google_connected || save.isPending} onClick={() => chooseFolder(current)}>
                Use {current.name}
              </button>
            </div>
          </div>
        </section>

        <section className="settings-section">
          <div className="settings-section__number">03</div>
          <div className="settings-section__intro">
            <span className="eyebrow">Primary Kindle route</span>
            <h2>Send over Wi-Fi</h2>
            <p>Kindrop copies directly into KOReader over key-only SSH. Wake the Kindle and keep it on the same Wi-Fi when a delivery is waiting.</p>
          </div>
          {draft && (
            <form
              className="settings-section__body settings-form"
              onSubmit={(event) => { event.preventDefault(); save.mutate(draft); }}
            >
              <div className="ssh-route-card">
                <div className="ssh-route-card__mark"><Radio size={22} /></div>
                <div>
                  <span className="eyebrow">KOReader SSH</span>
                  <strong>{currentSshStatus?.reachable === true ? "Kindle reachable" : currentSshStatus?.reachable === false ? "Kindle needs attention" : "Ready to test"}</strong>
                  <small>{currentSshStatus?.detail ?? "The connection test also checks the destination and available space."}</small>
                </div>
                <StatusBadge status={currentSshStatus?.reachable === true ? "verified" : currentSshStatus?.reachable === false ? "action_required" : "pending"} />
              </div>
              <div className="form-pair">
                <label>Kindle address
                  <input required value={draft.ssh_host ?? ""} placeholder="192.168.1.53" onChange={(event) => setDraft({ ...draft, ssh_host: event.target.value })} />
                </label>
                <label>SSH port
                  <input type="number" min="1" max="65535" required value={draft.ssh_port ?? 2222} onChange={(event) => setDraft({ ...draft, ssh_port: Number(event.target.value) })} />
                </label>
              </div>
              <label>KOReader destination
                <input required value={draft.ssh_destination ?? "/mnt/us/documents/KOReader/Kindrop"} onChange={(event) => setDraft({ ...draft, ssh_destination: event.target.value })} />
              </label>
              <label>Kindle profile
                <select value={draft.preset.kindle_profile} onChange={(event) => setDraft({ ...draft, preset: { ...draft.preset, kindle_profile: event.target.value } })}>
                  {profiles.data?.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
              </label>
              <div className="form-pair">
                <label>Reading direction
                  <select value={draft.preset.reading_direction} onChange={(event) => setDraft({ ...draft, preset: { ...draft.preset, reading_direction: event.target.value as "rtl" | "ltr" } })}>
                    <option value="rtl">Right to left</option><option value="ltr">Left to right</option>
                  </select>
                </label>
                <label>Double-page spreads
                  <select value={draft.preset.spread_mode} onChange={(event) => setDraft({ ...draft, preset: { ...draft.preset, spread_mode: event.target.value as Settings["preset"]["spread_mode"] } })}>
                    <option value="both">Split + rotate</option><option value="split">Split</option><option value="rotate">Rotate</option>
                  </select>
                </label>
              </div>
              <label>Crop behavior
                <select value={draft.preset.crop_mode} onChange={(event) => setDraft({ ...draft, preset: { ...draft.preset, crop_mode: event.target.value as Settings["preset"]["crop_mode"] } })}>
                  <option value="margins_and_page_numbers">Margins + page numbers</option><option value="margins">Margins only</option><option value="none">Do not crop</option>
                </select>
              </label>
              {currentSshStatus && (
                <dl className="ssh-facts">
                  <div><dt>Endpoint</dt><dd>{currentSshStatus.host ?? draft.ssh_host ?? "—"}:{currentSshStatus.port ?? draft.ssh_port ?? 2222}</dd></div>
                  <div><dt>Destination</dt><dd>{currentSshStatus.destination ?? draft.ssh_destination ?? "—"}</dd></div>
                  <div><dt>Capacity</dt><dd>{currentSshStatus.free_bytes === null ? "Unknown until tested" : `${formatBytes(currentSshStatus.free_bytes)} free`}</dd></div>
                </dl>
              )}
              {hostKey && (
                <div className="notice notice--verifying">
                  <div>
                    <strong>Compare the Kindle fingerprints</strong><br />
                    <small>Currently trusted: <code>{hostKey.trusted_fingerprint ?? "none"}</code></small><br />
                    <small>Presented now: <code>{hostKey.fingerprint}</code></small>
                  </div>
                  <button type="button" className="button button--secondary" onClick={() => trustHostKey.mutate(hostKey.fingerprint)} disabled={trustHostKey.isPending}>
                    {hostKey.trusted_fingerprint ? "Trust new Kindle key" : "Trust this Kindle key"}
                  </button>
                </div>
              )}
              <div className="settings-actions">
                <button className="button button--primary" disabled={save.isPending}><Save size={17} /> {save.isPending ? "Saving settings…" : "Save SSH destination"}</button>
                <button type="button" className="button button--secondary" disabled={inspectHostKey.isPending} onClick={() => inspectHostKey.mutate()}>
                  <FileKey size={17} /> {inspectHostKey.isPending ? "Reading identity…" : "Inspect SSH identity"}
                </button>
                <button type="button" className="button button--secondary" disabled={sshTest.isPending} onClick={() => sshTest.mutate()}>
                  <HardDrive size={17} /> {sshTest.isPending ? "Testing Kindle…" : "Test Kindle connection"}
                </button>
              </div>
              <p className="quiet-copy">Reserve <strong>{draft.ssh_host}</strong> for this Kindle in your router’s DHCP settings so the address remains stable.</p>
              {(save.error || inspectHostKey.error || trustHostKey.error || sshTest.error || sshStatus.error) && <p className="form-error">{(save.error || inspectHostKey.error || trustHostKey.error || sshTest.error || sshStatus.error)?.message}</p>}
            </form>
          )}
        </section>

        <section className="settings-section settings-section--fallback">
          <div className="settings-section__number">04</div>
          <div className="settings-section__intro">
            <span className="eyebrow">Manual email fallback</span>
            <h2>A second route, only when asked</h2>
            <p>If the Kindle is unavailable or full, History can send an individual document through Amazon instead. Kindrop never chooses email automatically.</p>
          </div>
          {draft && (
            <form className="settings-section__body settings-form fallback-form" onSubmit={(event) => { event.preventDefault(); save.mutate(draft); }}>
              <div className="fallback-form__heading"><Mail size={20} /><strong>Send to Kindle email</strong></div>
              <label>Fallback address
                <input type="email" value={draft.kindle_email ?? ""} placeholder="reader_123@kindle.com" onChange={(event) => setDraft({ ...draft, kindle_email: event.target.value || null })} />
              </label>
              <p className="quiet-copy">The connected Google account must remain in Amazon’s Approved Personal Document Email List.</p>
              <button className="button button--secondary" disabled={save.isPending}><Save size={17} /> Save email fallback</button>
            </form>
          )}
        </section>

        <section className="settings-section settings-section--quiet">
          <div className="settings-section__number">05</div>
          <div className="settings-section__intro"><span className="eyebrow">Housekeeping</span><h2>Tidy the workshop</h2><p>Clearing the cache removes source archives and unfinished EPUBs; history remains. Clearing history removes jobs, batches and deliveries; already-sent files are not proposed again.</p></div>
          <div className="settings-section__body">
            <div className="housekeeping-actions">
              <button className="button button--danger" onClick={() => purge.mutate()} disabled={purge.isPending}><Trash2 size={16} /> {purge.isPending ? "Clearing cache…" : "Clear temporary cache"}</button>
              <button
                className="button button--danger"
                onClick={() => {
                  if (window.confirm("Delete every job, batch and delivery record? Already-sent files will not reappear on the next scan.")) clearHistory.mutate();
                }}
                disabled={clearHistory.isPending}
              >
                <History size={16} /> {clearHistory.isPending ? "Clearing history…" : "Clear history"}
              </button>
            </div>
            {(purge.error || clearHistory.error) && <p className="form-error">{(purge.error || clearHistory.error)?.message}</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
