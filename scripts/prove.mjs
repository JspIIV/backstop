// Prove Backstop end to end on GenLayer Asimov.
//
//   AT=0x... PADV=<padv pw> PPUB=<ppub pw> node scripts/prove.mjs
//
// padv (the buyer) opens SLAs against ppub (the provider) and escrows the payment. An
// SLA whose status page shows the target met is released to the provider; one that shows
// it missed is refunded to the buyer; an SLA cannot be settled before its window; an
// unreadable status page is UNCLEAR and moves nothing.
import { Wallet } from 'ethers';
import { createClient, createAccount } from 'genlayer-js';
import { testnetAsimov } from 'genlayer-js/chains';
import fs from 'fs';
import os from 'os';
import path from 'path';
import url from 'url';

const AT = process.env.AT;
const PADV = process.env.PADV || '';
const PPUB = process.env.PPUB || '';
if (!AT || !PADV || !PPUB) { console.error('set AT, PADV and PPUB'); process.exit(1); }

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const KS = path.join(os.homedir(), '.genlayer', 'keystores');
async function acct(file, pw) {
  const w = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(KS, file), 'utf8'), pw);
  return { addr: w.address.toLowerCase(), client: createClient({ chain: testnetAsimov, account: createAccount(w.privateKey) }) };
}
const padv = await acct('padv.json', PADV);   // buyer
const ppub = await acct('ppub.json', PPUB);    // provider
const anybody = createClient({ chain: testnetAsimov });

const RAW = 'https://raw.githubusercontent.com/JspIIV/backstop/master/docs/';
const now = () => Math.floor(Date.now() / 1000);
const SOON = () => String(now() + 70);
const TARGET = '99.9% uptime with no single outage longer than one hour, across the window';
const SERVICE = 'Example API';
const GOOD = RAW + 'status-good.txt';
const BAD = RAW + 'status-bad.txt';
const UNREADABLE = RAW + 'no-such-status-9f2c.txt';

const out = [];
const say = l => { console.log(l); out.push(l); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const transient = e => /-32005|-32006|-32029|-32603|at capacity|rate limit|gas rate|reverted.*consensus|consensus.*reverted|backpressure|fetch failed|timeout|502|503|429|ECONNRESET|ENOTFOUND|EAI_AGAIN|getaddrinfo|resource not found/i
  .test(String(e?.details || e?.shortMessage || e?.message || e) + ' ' + String(e?.cause?.cause?.code || e?.cause?.code || ''));

async function read(fn, args = []) {
  for (let a = 1; ; a++) {
    try { return JSON.parse(await anybody.readContract({ address: AT, functionName: fn, args })); }
    catch (e) { if (!transient(e) || a >= 14) throw e; await sleep(5000 * a); }
  }
}
async function write(who, fn, args) {
  for (let a = 1; ; a++) {
    try { return await who.client.writeContract({ address: AT, functionName: fn, args, value: 0n }); }
    catch (e) { if (!transient(e) || a >= 14) throw e; say(`  (${fn} transient, wait ${8 * a}s)`); await sleep(8000 * a); }
  }
}
async function openSla(status_url, escrow, window) {
  const n = (await read('size')).total;
  for (let attempt = 1; attempt <= 3; attempt++) {
    await write(padv, 'open_sla', [ppub.addr, SERVICE, status_url, TARGET, window, String(escrow)]);
    for (let i = 0; i < 30; i++) { const s = await read('size'); if (s.total > n) return String(s.total - 1); await sleep(5000); }
  }
  throw new Error('SLA not opened');
}
async function settleUntil(who, id, label) {
  const before = Number((await read('get', [id])).settlements || 0);
  for (let attempt = 1; attempt <= 4; attempt++) {
    try { await write(who, 'settle', [id]); } catch (e) { say(`  ${label} err ${String(e.message).slice(0, 50)}`); }
    for (let i = 0; i < 36; i++) {
      await sleep(15000);
      const g = await read('get', [id]);
      if (Number(g.settlements || 0) > before) { say(`  ${label}: ${g.status} v=${g.verdict} (${(i + 1) * 15}s)`); return g; }
    }
    say(`  ${label}: not settled after poll, retrying`);
  }
  return await read('get', [id]);
}

say('Backstop, proven on GenLayer Asimov');
say('  contract ' + AT);
say('  buyer(padv) ' + padv.addr + '  provider(ppub) ' + ppub.addr);
say('');

const baseProv = (await read('balance', [ppub.addr])).balance;
const baseBuyer = (await read('balance', [padv.addr])).balance;
const baseSize = await read('size');

const win = SOON();
const slaMet = await openSla(GOOD, 100, win);
say('opened #' + slaMet + ' (status shows uptime met) escrow 100');
const slaMiss = await openSla(BAD, 250, win);
say('opened #' + slaMiss + ' (status shows an outage) escrow 250');
const slaHeld = await openSla(UNREADABLE, 70, win);
say('opened #' + slaHeld + ' (unreadable status page) escrow 70');
say('');

say('trying to settle #' + slaMet + ' before its window closes...');
try { await write(padv, 'settle', [slaMet]); } catch {}
await sleep(6000);
const early = await read('get', [slaMet]);
say('  #' + slaMet + ' status after early settle: ' + early.status);
say('');

const waitLeft = Number(win) + 8 - now();
if (waitLeft > 0) { say('waiting ' + waitLeft + 's for the window to close...'); await sleep(waitLeft * 1000); }

say('settling #' + slaMet + ' (uptime met)...');
const rMet = await settleUntil(ppub, slaMet, 'met');
say('  #' + slaMet + ' status ' + rMet.status + ' | ' + (rMet.reason || ''));
say('settling #' + slaMiss + ' (outage)...');
const rMiss = await settleUntil(padv, slaMiss, 'missed');
say('  #' + slaMiss + ' status ' + rMiss.status + ' | ' + (rMiss.reason || ''));
say('settling #' + slaHeld + ' (unreadable)...');
const rHeld = await settleUntil(ppub, slaHeld, 'held');
say('  #' + slaHeld + ' status ' + rHeld.status + ' | verdict ' + rHeld.verdict);
say('');

const prov = (await read('balance', [ppub.addr])).balance;
const buyer = (await read('balance', [padv.addr])).balance;
const size = await read('size');
say('provider balance ' + baseProv + ' -> ' + prov + ' (releases received)');
say('buyer balance ' + baseBuyer + ' -> ' + buyer + ' (refunds received)');
say('book: ' + JSON.stringify(size));

const checks = [
  ['a status page showing the target met RELEASES the escrow to the provider', rMet.status === 'RELEASED'],
  ['the escrow is credited to the provider', prov - baseProv === 100],
  ['a status page showing an outage REFUNDS the escrow to the buyer', rMiss.status === 'REFUNDED'],
  ['the escrow is refunded to the buyer', buyer - baseBuyer === 250],
  ['an SLA cannot be settled before its window closes, it stays funded', early.status === 'FUNDED'],
  ['an unreadable status page is UNCLEAR and moves nothing', rHeld.status === 'FUNDED' && rHeld.verdict === 'UNCLEAR'],
  ['this run adds one release and one refund to the book',
    size.released - baseSize.released === 1 && size.refunded - baseSize.refunded === 1],
];
say('');
for (const [label, ok] of checks) say((ok ? '  ok   ' : ' FAIL  ') + label);
const failed = checks.filter(([, ok]) => !ok);
say('');
say(failed.length ? `${failed.length} of ${checks.length} checks failed` : `${checks.length} checks. The status page released the escrow or refunded it, and neither party settled it themselves.`);

fs.mkdirSync(path.join(ROOT, 'results'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'results', 'proved.json'), JSON.stringify({
  proved_at: new Date().toISOString(), network: 'genlayer testnet asimov', contract: AT,
  released: rMet, refunded: rMiss, held: rHeld, early,
  provider_balance: prov, buyer_balance: buyer, size,
  checks: checks.map(([label, ok]) => ({ label, ok })), transcript: out,
}, null, 2));
say('Written to results/proved.json');
process.exit(failed.length ? 1 : 0);
