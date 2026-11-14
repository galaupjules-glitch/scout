/* Version « sans serveur » : tout se passe dans le navigateur du candidat,
   et le dossier final est envoyé dans le salon Discord #applications via un webhook.
   __WEBHOOK__, __ADMIN__ et __CFG__ sont remplacés par build.py. */
(function(){
const WEBHOOK = "__WEBHOOK__", ADMIN_SECRET = "__ADMIN__", CFG = __CFG__, TEXTES = __TEXTES__;
const ORDRE = ["identite","frappe","experience","agence","shifts","resultats","termine"];
const KEY = "vexa_application";
let C = null; try{ C = JSON.parse(localStorage.getItem(KEY) || "null") }catch(e){}
const FILES = new Map(); // nom -> dataURL (en mémoire seulement)
if(C && C.data) C.data.fichiers = [];
const save = () => { try{ localStorage.setItem(KEY, JSON.stringify(C)) }catch(e){} };
const pub = () => ({id:C.id, etape:C.etape, statut:C.statut, prenom:(C.data.identite||{}).prenom||"", essais:((C.data.frappe||{}).essais||[]).length, fichiers:C.data.fichiers||[]});
const norm = t => t.replace(/[’]/g,"'").replace(/[“”]/g,'"').replace(/\s+/g," ").trim();
function lev(a,b){let p=Array.from({length:b.length+1},(_,i)=>i);for(let i=1;i<=a.length;i++){const c=[i];for(let j=1;j<=b.length;j++)c[j]=Math.min(p[j]+1,c[j-1]+1,p[j-1]+(a[i-1]===b[j-1]?0:1));p=c}return p[b.length]}
function noter(ref,tape,sec){ref=norm(ref);tape=norm(tape);const m=Math.max(sec,1)/60,brut=tape.length/5/m;
  const prec=(1-lev(ref,tape)/Math.max(ref.length,tape.length,1))*100, net=brut*prec/100;
  return {wpm:+net.toFixed(1),wpm_brut:+brut.toFixed(1),precision:+prec.toFixed(1),secondes:+(+sec).toFixed(1),reussi:net>=CFG.wpm_min&&prec>=CFG.precision_min,suspect:brut>=150}}
const ok = j => new Response(JSON.stringify(j),{status:200,headers:{"Content-Type":"application/json"}});
const ko = e => new Response(JSON.stringify({erreur:e}),{status:400,headers:{"Content-Type":"application/json"}});
const isAdmin = () => location.hash === "#admin-" + ADMIN_SECRET;

/* ---------- envoi Discord ---------- */
const cut = (s, n=1024) => { s = String(s || "—").trim() || "—"; return s.length > n ? s.slice(0, n-1) + "…" : s };
async function villeGPS(g){
  try{
    const ctl = new AbortController(); setTimeout(() => ctl.abort(), 4000);
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&zoom=10&accept-language=en&lat=${g.lat}&lon=${g.lon}`, {signal:ctl.signal});
    const a = (await r.json()).address || {};
    return [a.city || a.town || a.village || a.municipality || a.county, a.country].filter(Boolean).join(", ");
  }catch(e){ return "" }
}
async function envoyerDiscord(){
  const d = C.data, i = d.identite || {}, g = i.geo || {}, a = d.agence || {}, f = d.frappe || {};
  const best = (f.essais || []).slice().sort((x,y) => (y.reussi - x.reussi) || (y.wpm - x.wpm))[0];
  const st = {complet:["📋 New application", 0x57F287], stop_debutant:["🌱 Stopped: beginner", 0xE8C46A], stop_frappe:["⌨️ Stopped: typing test failed", 0xEF7D76]}[C.statut] || ["Application", 0xC9A86A];
  const ville = g.lat ? await villeGPS(g) : "";
  const flags = []; if(best && best.suspect) flags.push("abnormal speed"); if((f.essais||[]).some(e => e.colle)) flags.push("tried to paste");
  const shifts = (d.shifts && d.shifts.shifts || []).map(id => { const s = CFG.shifts.find(x => x.id === id); return s ? `${s.nom} (${s.heures})` : id }).join(", ");
  const fields = [
    {name:"👤 Profile", value:cut(`**${i.prenom} ${i.nom}**, ${i.age} y/o\nDiscord: **${i.contact}**\nLanguages: ${i.langues}`)},
    {name:"📍 Location", value:cut(`Declared: ${i.ville}, ${i.pays}\nGPS: ${g.lat ? `[${g.lat}, ${g.lon}](https://www.google.com/maps?q=${g.lat},${g.lon}) ±${g.precision} m${ville ? `\nGPS city: **${ville}**` : ""}` : "not shared"}\nTimezone: ${i.fuseau || "—"}`)},
    {name:"⌨️ Typing test", value:best ? cut(`${best.reussi ? "✅" : "❌"} **${Math.round(best.wpm)} WPM** · ${best.precision}% · ${best.secondes}s\nAttempts: ${(f.essais||[]).map(e => Math.round(e.wpm)).join(" / ")} WPM${flags.length ? `\n⚠️ ${flags.join(", ")}` : ""}`) : "not taken"},
    {name:"💼 Experience", value:{experimente:"Experienced", debutant:"Beginner"}[d.experience && d.experience.experience] || "—", inline:true},
  ];
  if(d.agence) fields.push({name:"🏢 Agency & numbers", value:cut(`**${a.agence}** · ${a.duree}\nCreators: ${a.creatrices}\nRevenue/shift: **${a.ca_shift}**\nBest month: ${a.ca_mois || "—"}\nBest result: ${a.meilleur || "—"}\nWhy leaving: ${a.depart || "—"}`)});
  if(d.shifts) fields.push({name:"⏰ Shifts", value:cut(`${shifts}\nPer week: ${d.shifts.par_semaine || "—"}\nConstraints: ${d.shifts.dispo || "—"}`)});
  if(d.resultats && d.resultats.note) fields.push({name:"💬 Comment", value:cut(d.resultats.note)});
  const noms = [...FILES.keys()];
  if(C.statut === "complet") fields.push({name:"📈 Screenshots", value:String(noms.length), inline:true});
  const emb = {title:cut(`${C.test ? "[TEST] " : ""}${st[0]} · ${i.prenom || ""} ${i.nom || ""}`, 250), color:st[1], fields, timestamp:new Date().toISOString(), footer:{text:"Vexa Chatter Application"}};
  if(noms.length) emb.image = {url:`attachment://${noms[0]}`};
  const fd = new FormData();
  fd.append("payload_json", JSON.stringify({username:"Vexa Applications", embeds:[emb], allowed_mentions:{parse:[]}, attachments:noms.map((n,k) => ({id:k, filename:n}))}));
  noms.forEach((n,k) => { const b64 = FILES.get(n).split(",")[1], bin = atob(b64), u = new Uint8Array(bin.length); for(let j=0;j<bin.length;j++) u[j] = bin.charCodeAt(j); fd.append(`files[${k}]`, new Blob([u], {type:"image/jpeg"}), n) });
  for(let t = 0; t < 3; t++){
    const r = await fetch(WEBHOOK + "?wait=true", {method:"POST", body:fd}).catch(() => null);
    if(r && r.ok) return true;
    if(r && r.status === 429){ await new Promise(s => setTimeout(s, 2000)); continue }
    if(r && r.status === 413) throw new Error("Your screenshots are too heavy. Remove a few and try again.");
    await new Promise(s => setTimeout(s, 1000));
  }
  throw new Error("We couldn't send your application. Check your connection and try again.");
}

/* ---------- « API » locale ---------- */
const realFetch = window.fetch.bind(window);
window.fetch = async (url, opt) => {
  const u = String(url).split("?")[0];
  if(!u.startsWith("/api/")) return realFetch(url, opt);
  const d = opt && opt.body ? JSON.parse(opt.body) : {};
  if(u === "/api/config") return ok(CFG);
  if(u === "/api/texte"){ const i = Math.floor(Math.random() * TEXTES.length); return ok({idx:i, texte:TEXTES[i]}) }
  if(u === "/api/moi") return C ? ok(pub()) : ko("unknown");
  if(u === "/api/start"){ C = {id:String(Date.now()), etape:"identite", statut:"en_cours", data:{}}; FILES.clear(); save(); return ok(pub()) }
  if(u === "/api/admin/aller"){
    if(!isAdmin()) return ko("Not allowed.");
    C.test = true; C.etape = d.etape; C.statut = d.etape === "termine" ? (d.statut || "en_cours") : "en_cours";
    if(d.etape === "frappe") delete C.data.frappe; save(); return ok(pub());
  }
  if(u === "/api/fichier"){
    C.data.fichiers = C.data.fichiers || [];
    if(C.data.fichiers.length >= CFG.max_fichiers) return ko(`${CFG.max_fichiers} screenshots maximum.`);
    const n = String(C.data.fichiers.length + 1).padStart(2, "0") + "_" + Math.random().toString(36).slice(2, 6) + ".jpg";
    FILES.set(n, d.data); C.data.fichiers.push(n); return ok(pub());
  }
  if(u === "/api/fichier/supprimer"){ FILES.delete(d.nom); C.data.fichiers = (C.data.fichiers || []).filter(n => n !== d.nom); return ok(pub()) }
  if(u === "/api/etape"){
    const e = d.etape, v = d.data || {};
    if(C.statut !== "en_cours") return ko("Your application is already finished.");
    if(e !== C.etape) return ko("Unexpected step, please reload the page.");
    if(e === "frappe"){
      const f = C.data.frappe = C.data.frappe || {essais:[]};
      if(f.essais.length >= CFG.essais) return ko("You've used all your attempts.");
      const r = noter(TEXTES[v.texte] || "", v.tape || "", v.secondes || 1); r.colle = !!v.colle; f.essais.push(r);
      const reste = CFG.essais - f.essais.length, passe = f.essais.some(x => x.reussi);
      if(!r.reussi && reste > 0){ save(); return ok({...pub(), resultat:r, reste, avance:false}) }
      if(!passe){ C.statut = "stop_frappe"; C.etape = "termine"; save(); envoyerDiscord().catch(()=>{}); return ok({...pub(), resultat:r, reste:0, avance:false, stop:true}) }
      C.etape = "experience"; save(); return ok({...pub(), resultat:r, reste, avance:true});
    }
    if(e === "identite"){ if(!v.geo || v.geo.statut !== "partagee") return ko("Share your location to continue.") }
    C.data[e] = v;
    if(e === "experience" && v.experience === "debutant"){ C.statut = "stop_debutant"; C.etape = "termine"; save(); envoyerDiscord().catch(()=>{}); return ok({...pub(), stop:true}) }
    if(e === "resultats"){
      C.statut = "complet";
      try{ await envoyerDiscord() }catch(err){ C.statut = "en_cours"; return ko(err.message) }
    }
    C.etape = ORDRE[ORDRE.indexOf(e) + 1]; save(); return ok(pub());
  }
  return ko("Not found.");
};
})();
