// Checks the page's pure logic against the wiki tables. Run: node plan.test.js
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/index.html", "utf8").split("\r\n").join("\n");
const D = JSON.parse(html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const src = html.match(/const fmt =[\s\S]*?(?=\/\/ =+ State)/)[0];
const MAX = 21;
const { plan, setBonuses, fitsTier, encodeBuild, decodeBuild, equipSet, runeEnchantTotals, enchantValues, splitBonus, buildTotals } =
  new Function("D", "MAX", src + "\nreturn { plan, setBonuses, fitsTier, encodeBuild, decodeBuild, equipSet, runeEnchantTotals, enchantValues, splitBonus, buildTotals };")(D, MAX);
const tot = p => Object.fromEntries(p.totals.map(t => [t.name, t.amount]));

// --- Ancient planner ---
// T18 +0 -> T19 +4: two full upgrades + one uptier
let t = tot(plan(18, 0, 19, 4, true));
assert.deepStrictEqual(t, { Gold: 2e6, "Epic Material": 200, Yogurth: 2500, "Radioactive Fries": 3500, "Slime Spoon": 1, "Soda Can": 1, "Little Mushroom": 1 });
// same without the disputed 1000 legendary per uptier
assert.strictEqual(tot(plan(18, 0, 19, 4, false))["Radioactive Fries"], 2500);
// T8 -> T9 uptier never costs legendary mats, uses the four Cumuli
t = tot(plan(8, 4, 9, 0, true));
assert.deepStrictEqual(Object.keys(t).sort(), ["Aevum Cumuli", "Glacius Cumuli", "Septos Cumuli", "Terbirs Cumuli"]);
// partial: T21 +2 -> +4 costs only levels 3 and 4
assert.deepStrictEqual(tot(plan(21, 2, 21, 4, true)), { Gold: 7e5, "Epic Material": 70, "Insight Glasses": 1750 });
// full Harvester run T8 +0 -> T21 +4: 14 tiers of upgrades, 13 uptiers
const p = plan(8, 0, 21, 4, true);
assert.strictEqual(p.steps.filter(s => s.kind === "uptier").length, 13);
assert.strictEqual(tot(p).Gold, 14e6);

// --- Build planner ---
const night = D.gear.filter(g => g.set === "Night_Walker");          // 4-piece set: 2/4, 3/4, 4/4
const starweave = D.gear.find(g => g.id === "a-starweave-ring-ring");
assert.strictEqual(night.length, 4);
let s = setBonuses(night.slice(0, 2))[0];
assert.deepStrictEqual([s.pieces, s.effective, s.bonuses.map(b => b.active)], [2, 2, [true, false, false]]);
// Starweave adds one piece once 2+ are worn, but not to a lone piece
s = setBonuses([...night.slice(0, 2), starweave])[0];
assert.deepStrictEqual([s.effective, s.bonuses.map(b => b.active)], [3, [true, true, false]]);
assert.strictEqual(setBonuses([night[0], starweave])[0].effective, 1);
// tier filter: exact tier for set/mythic, "All" always, ancients from their start tier up
const t18set = D.gear.find(g => g.kind === "set" && g.tier === "T18");
assert.ok(fitsTier(t18set, 18) && !fitsTier(t18set, 19) && fitsTier(t18set, "any"));
assert.ok(fitsTier(starweave, 21) && !fitsTier(starweave, 8));
assert.ok(fitsTier(D.gear.find(g => g.tier === "All"), 5));
// share link round trip, and junk ids in a link are dropped instead of breaking the page
const empty6 = [[], [], [], [], [], []];
const b = { tier: 18, slots: { Mainhand: "a-sword-of-escarium-mainhand", Ring: starweave.id, Accessory: D.accessories[0].id },
            runes: { meta: "r-meta-gerou", major1: "r-major-ogok", major4: "r-major-ogok" }, enchants: [["Damage"], ["Damage", "Speed"], [], [], [], []] };
assert.deepStrictEqual(decodeBuild(encodeBuild(b)), b);
assert.ok(/^[A-Za-z0-9._~-]+$/.test(encodeBuild(b)), "hash must use only characters the artifact viewer passes through");
// old links (gear only) still open; a rune in the wrong slot type is dropped
assert.deepStrictEqual(decodeBuild("b~18~nope~0~0~0~0~0~0"), { tier: 18, slots: {}, runes: {}, enchants: empty6 });
const wrong = encodeBuild({ ...b, runes: { meta: "r-major-ogok" } });
assert.deepStrictEqual(decodeBuild(wrong).runes, {});

// --- Runes & enchants ---
// one enchant bonus = large value, two = small value each
assert.deepStrictEqual(enchantValues(["Damage"]), [{ stat: "Damage", value: 2.5 }]);
assert.deepStrictEqual(enchantValues(["Damage", "Speed"]), [{ stat: "Damage", value: 1.25 }, { stat: "Speed", value: 1.25 }]);
// 2x Ogok (3% each) + Damage enchant 2.5% + 1.25% = 9.75% Damage; Elementarium doubles the rune part only
let tt = runeEnchantTotals(b.runes, b.enchants, false);
assert.strictEqual(tt.stats.Damage, 9.75);
assert.strictEqual(tt.stats.Speed, 1.25);
assert.deepStrictEqual(tt.effects.map(r => r.name), ["Gerou"]);
assert.strictEqual(runeEnchantTotals(b.runes, b.enchants, true).stats.Damage, 15.75);
assert.strictEqual(decodeBuild("garbage"), null);
// set preset: equips all 4 Aeneas pieces, keeps other slots, activates every bonus
const kept = { Offhand: "m-leonidas-savior", Accessory: D.accessories[0].id };
const slots = equipSet(kept, "Aeneas");
assert.strictEqual(slots.Offhand, "m-leonidas-savior");
const aeneas = Object.values(slots).map(id => D.gear.find(g => g.id === id)).filter(g => g && g.set === "Aeneas");
assert.strictEqual(aeneas.length, 4);
assert.ok(setBonuses(aeneas)[0].bonuses.every(b => b.active));
// every set preset fills one slot per piece and never two pieces into one slot
for (const key of Object.keys(D.sets)) {
  const filled = Object.values(equipSet({}, key)).map(id => D.gear.find(g => g.id === id));
  assert.ok(filled.length >= 1 && filled.every(g => g.set === key), key);
}
// --- INFO totals ---
// flat parts are summed, conditional text is kept as an effect
assert.deepStrictEqual(splitBonus("7% Dual Strike, 7% Empower Chance, 50% Crit Chance"),
  { flats: [{ stat: "Dual Strike", value: 7 }, { stat: "Empower Chance", value: 7 }, { stat: "Critical Chance", value: 50 }], rest: "" });
let sb = splitBonus("30% chance to apply a Death mark · Element bonus: +4% Water Resistance and +4% Electric Damage");
assert.deepStrictEqual(sb.flats, [{ stat: "Water Resistance", value: 4 }, { stat: "Electric Damage", value: 4 }]);
assert.ok(sb.rest.startsWith("30% chance"));
assert.deepStrictEqual(splitBonus("Gain 5% enrage for each SP you spend, up to 25%.").flats, []);
// the build from the in-game screenshots: Aeneas 4/4 + Leonidas Savior + Leonidas Wings, plus runes/enchants
const mine = { tier: 18, slots: { Mainhand: "s-aeneas-wings-aeneas", Offhand: "m-leonidas-savior", Head: "s-aeneas-winged-helm-aeneas",
  Body: "s-aeneas-toga-aeneas", Neck: "s-aeneas-lighting-aeneas", Ring: "m-leonidas-wings" },
  runes: { major1: "r-major-ora", meta: "r-meta-gael" }, enchants: [["Speed"]] };
const tot2 = buildTotals(mine);
assert.strictEqual(tot2.stats["Speed Bonus"].total, 12.5 + 3 + 2.5);           // Aeneas (3/4) + Ora rune + Speed enchant
assert.strictEqual(tot2.stats["Water Resistance"].total, 4);                     // Leonidas Savior element bonus
assert.strictEqual(tot2.stats["Electric Damage"].total, 4);
assert.ok(tot2.effects.some(e => e.source.startsWith("Aeneas (4/4)")) && tot2.effects.some(e => e.source.startsWith("Gael")));
// Ancients at T12+ add 5% damage and damage reduction each
const anc = buildTotals({ tier: 18, slots: { Mainhand: "a-sword-of-escarium-mainhand" } });
assert.strictEqual(anc.stats["Damage Bonus"].total, 5);
assert.strictEqual(buildTotals({ tier: 11, slots: { Mainhand: "a-sword-of-escarium-mainhand" } }).stats["Damage Bonus"], undefined);
console.log("all ok");
