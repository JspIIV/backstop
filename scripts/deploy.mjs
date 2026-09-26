// Deploy the Trace contract to GenLayer Asimov with the ppub keystore.
//   PPUB=<ppub pw> node scripts/deploy.mjs
import { Wallet } from 'ethers';
import { createClient, createAccount } from 'genlayer-js';
import { testnetAsimov } from 'genlayer-js/chains';
import fs from 'fs';
import os from 'os';
import path from 'path';
import url from 'url';

const PPUB = process.env.PPUB || '';
if (!PPUB) { console.error('set PPUB'); process.exit(1); }

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const KS = path.join(os.homedir(), '.genlayer', 'keystores');
const code = fs.readFileSync(path.join(ROOT, 'contracts', 'backstop.py'), 'utf8');

const w = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(KS, 'ppub.json'), 'utf8'), PPUB);
const account = createAccount(w.privateKey);
const client = createClient({ chain: testnetAsimov, account });
console.log('deployer', w.address);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const transient = e => /-32005|-32006|-32603|at capacity|rate limit|gas rate|backpressure|fetch failed|timeout|502|503|429|ECONNRESET|ENOTFOUND|getaddrinfo/i
  .test(String(e?.details || e?.shortMessage || e?.message || e) + ' ' + String(e?.cause?.cause?.code || e?.cause?.code || ''));

let hash;
for (let a = 1; ; a++) {
  try { hash = await client.deployContract({ code, args: [] }); break; }
  catch (e) { if (!transient(e) || a >= 8) throw e; console.log(`  (deploy transient, wait ${6 * a}s)`); await sleep(6000 * a); }
}
console.log('tx', hash);

function addrFrom(r) {
  if (!r) return null;
  return r.contractAddress || r.recipient || r.data?.contract_address || r.data?.contractAddress
    || r.tx_data_decoded?.contract_address || r.consensus_data?.leader_receipt?.contract_address || null;
}

let addr = null;
for (let i = 0; i < 60; i++) {
  await sleep(5000);
  let rcpt = null;
  try { rcpt = await client.getTransactionReceipt({ hash, status: 'FINALIZED', retries: 1, interval: 1000 }); }
  catch { try { rcpt = await client.getTransaction({ hash }); } catch {} }
  addr = addrFrom(rcpt);
  if (addr) { console.log('receipt status', rcpt?.status); break; }
  if (i === 0 && rcpt) console.log('receipt keys:', Object.keys(rcpt).join(', '));
}

if (!addr) { console.error('no contract address in receipt after polling; check tx', hash); process.exit(1); }
console.log('DEPLOYED', addr);
fs.writeFileSync(path.join(ROOT, 'deployment.json'),
  JSON.stringify({ network: 'genlayer testnet asimov', address: addr, tx: hash, deployed_at: new Date().toISOString() }, null, 2));
console.log('written deployment.json');
