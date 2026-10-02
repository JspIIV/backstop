// Prove Backstop's real escrow, release and refund on GenLayer Studio, where native value moves.
//
//   PADV=<padv pw> PPUB=<ppub pw> node scripts/prove-studio.mjs
//   (optional AT=0x... to reuse a deployed contract)
//
// The buyer (padv) opens two SLAs against the provider (ppub), depositing the escrow as
// native value. One whose status page shows the target met is settled MET and the deposit
// goes to the provider; one that shows an outage is settled MISSED and the deposit is
// refunded to the buyer. The on-chain balances actually move.
import { Wallet } from 'ethers';
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';
import fs from 'fs';
import os from 'os';
import path from 'path';
import url from 'url';

const PADV = process.env.PADV || '';
const PPUB = process.env.PPUB || '';
if (!PADV || !PPUB) { console.error('set PADV and PPUB'); process.exit(1); }

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const KS = path.join(os.homedir(), '.genlayer', 'keystores');
const RPC = studionet.rpcUrls?.default?.http?.[0];
const code = fs.readFileSync(path.join(ROOT, 'contracts', 'backstop.py'), 'utf8');

async function acct(file, pw) {
  const w = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(KS, file), 'utf8'), pw);
  return { addr: w.address.toLowerCase(), client: createClient({ chain: studionet, account: createAccount(w.privateKey) }) };
}
const padv = await acct('padv.json', PADV);   // buyer
const ppub = await acct('ppub.json', PPUB);    // provider
const anybody = createClient({ chain: studionet });

const RAW = 'https://raw.githubusercontent.com/JspIIV/backstop/master/docs/';
const now = () => Math.floor(Date.now() / 1000);
const SOON = () => String(now() + 70);
const TARGET = '99.9% uptime with no single outage longer than one hour, across the window';
const SERVICE = 'Example API';
const GOOD = RAW + 'status-good.txt';
const BAD = RAW + 'status-bad.txt';
const ESCROW = 1000000000000000000n; // 1 GEN each

const out = [];
const say = l => { console.log(l); out.push(l); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function retry(fn, label){ for(let a=1;;a++){ try{ return await fn(); } catch(e){ if(a>=10) throw e; await sleep(4000*a); } } }
async function rpc(method, params){ return await retry(async ()=>{ const r = await fetch(RPC,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})}); return (await r.json()).result; }); }
async function fund(addr){ try{ await rpc('sim_fundAccount',[addr, '0x3635c9adc5dea00000']); }catch(e){} }
async function bal(addr){ return BigInt(await rpc('eth_getBalance',[addr,'latest'])); }
const read = async (AT, fn, args=[]) => retry(async ()=> JSON.parse(await anybody.readContract({ address: AT, functionName: fn, args })));
const transient = e => /-32005|-32006|-32603|at capacity|rate limit|backpressure|fetch failed|timeout|502|503|429|ECONNRESET|ENOTFOUND/i.test(String(e?.details||e?.shortMessage||e?.message||e));
async function write(who, AT, fn, args, value){
  for(let a=1;;a++){ try{ return await who.client.writeContract({ address: AT, functionName: fn, args, value: value||0n }); }
    catch(e){ if(!transient(e)||a>=8) throw e; say(`  (${fn} transient, wait ${5*a}s)`); await sleep(5000*a); } }
}
async function openSla(status_url, escrow, window){
  const n = (await read(AT,'size')).total;
  await write(padv, AT, 'open_sla', [ppub.addr, SERVICE, status_url, TARGET, window], escrow);
  for(let i=0;i<30;i++){ const s=await read(AT,'size'); if(s.total>n) return String(s.total-1); await sleep(4000); }
  throw new Error('SLA not opened');
}
async function settleUntil(who, id, label){
  const before=Number((await read(AT,'get',[id])).settlements||0);
  for(let attempt=1;attempt<=4;attempt++){
    try{ await write(who, AT, 'settle', [id]); }catch(e){ say(`  ${label} err ${String(e.message).slice(0,40)}`); }
    for(let i=0;i<40;i++){ await sleep(6000); const g=await read(AT,'get',[id]);
      if(Number(g.settlements||0)>before){ say(`  ${label}: ${g.status} v=${g.verdict} (${(i+1)*6}s)`); return g; } }
    say(`  ${label}: not settled, retrying`);
  }
  return await read(AT,'get',[id]);
}

say('Backstop real escrow, proven on GenLayer Studio');
say('  buyer(padv) ' + padv.addr + '  provider(ppub) ' + ppub.addr);
await fund(padv.addr); await fund(ppub.addr); await sleep(2000);

let AT = process.env.AT || null;
if(AT){ say('using existing contract ' + AT); }
else {
  say('deploying...');
  const hash = await padv.client.deployContract({ code, args: [] });
  say('  deploy tx ' + hash);
  for(let i=0;i<60;i++){ await sleep(5000);
    let t=null; try{ t = await padv.client.getTransaction({hash}); }catch{}
    const a = t?.recipient || t?.data?.contract_address || t?.contractAddress || null;
    if(a){ try{ await read(a,'size'); AT=a; break; }catch{} }
  }
  if(!AT){ console.error('deploy address not found; tx '+hash); process.exit(1); }
  say('deployed ' + AT);
}
say('');

const cBal0 = await bal(AT);
const win = SOON();
say('opening two SLAs, depositing 1 GEN escrow each...');
const slaMet = await openSla(GOOD, ESCROW, win);
const slaMiss = await openSla(BAD, ESCROW, win);
const cBal1 = await bal(AT);
say('  #' + slaMet + ' (good status) and #' + slaMiss + ' (outage) opened');
say('  contract balance ' + cBal0 + ' -> ' + cBal1 + ' (two deposits held)');
say('');

const waitLeft = Number(win) + 8 - now();
if (waitLeft > 0) { say('waiting ' + waitLeft + 's for the window to close...'); await sleep(waitLeft * 1000); }

// MET: settle by the buyer so the provider pays no gas while we measure its balance.
const provBefore = await bal(ppub.addr);
say('settling the good SLA #' + slaMet + ' (should release to the provider)...');
const rMet = await settleUntil(padv, slaMet, 'met');
let provAfter = provBefore;
for(let i=0;i<30;i++){ await sleep(6000); provAfter = await bal(ppub.addr); if(provAfter-provBefore>=ESCROW) { say('  release landed ~'+((i+1)*6)+'s'); break; } }
say('  provider balance ' + provBefore + ' -> ' + provAfter + ' (delta ' + (provAfter-provBefore) + ')');

// MISSED: settle by the provider so the buyer pays no gas while we measure its balance.
const buyerBefore = await bal(padv.addr);
say('settling the outage SLA #' + slaMiss + ' (should refund the buyer)...');
const rMiss = await settleUntil(ppub, slaMiss, 'missed');
let buyerAfter = buyerBefore;
for(let i=0;i<30;i++){ await sleep(6000); buyerAfter = await bal(padv.addr); if(buyerAfter-buyerBefore>=ESCROW) { say('  refund landed ~'+((i+1)*6)+'s'); break; } }
say('  buyer balance ' + buyerBefore + ' -> ' + buyerAfter + ' (delta ' + (buyerAfter-buyerBefore) + ')');
const cBal2 = await bal(AT);
say('  contract balance now ' + cBal2 + ' (both deposits paid out)');
say('');

const size = await read(AT,'size');
say('book: ' + JSON.stringify(size));

const checks = [
  ['opening two SLAs deposits both escrows into the contract', cBal1 - cBal0 === ESCROW * 2n],
  ['a status page showing the target met releases to the provider', rMet.status === 'RELEASED'],
  ['the deposit actually reaches the provider on chain', provAfter - provBefore === ESCROW],
  ['a status page showing an outage refunds the buyer', rMiss.status === 'REFUNDED'],
  ['the deposit actually returns to the buyer on chain', buyerAfter - buyerBefore === ESCROW],
  ['both deposits leave the contract', cBal2 === cBal0],
];
say('');
for (const [label, ok] of checks) say((ok ? '  ok   ' : ' FAIL  ') + label);
const failed = checks.filter(([, ok]) => !ok);
say('');
say(failed.length ? `${failed.length} of ${checks.length} checks failed` : `${checks.length} checks. The status page released the real deposit to the provider or refunded it to the buyer.`);

fs.mkdirSync(path.join(ROOT, 'results'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'results', 'proved-studio.json'), JSON.stringify({
  proved_at: new Date().toISOString(), network: 'genlayer studionet', contract: AT,
  escrow_wei: ESCROW.toString(), deposited: (cBal1-cBal0).toString(),
  provider_delta: (provAfter-provBefore).toString(), buyer_delta: (buyerAfter-buyerBefore).toString(),
  met: rMet, missed: rMiss, size, checks: checks.map(([label, ok]) => ({ label, ok })), transcript: out,
}, null, 2));
say('Written to results/proved-studio.json');
process.exit(failed.length ? 1 : 0);
