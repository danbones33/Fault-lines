/* The tutorial scripts (v3). Plain explanation, slow shots, every clip plays to the end, nothing written over the app.
   Each step: {say?, mood?, do?(T), gap?}. A step's narration and its actions run together. Every fact is checked against
   the run at the round on screen (comments). */

const TOUR_RUNS = {
  water: "20261023-143351-3504",      // Colorado River election: Voss vs Brandt (rounds 0-6, election night = 7)
  season2: "20261023-173056-7540",    // Season 2: President Eli Brandt (quarters 1-16, election night = 17)
  aria: "20261023-183155-2683",       // ARIA vs Pastor Ruth Calloway (rounds 0-6, election night = 7)
};

const WATER_TEXT = "BREAKING: The Colorado River Compact collapses. After a fourth straight failed snowpack, the Bureau of Reclamation cuts water allocations to Arizona, Nevada and California by 40 percent and orders farm districts in the Imperial Valley fallowed. The White House invokes emergency powers to seize state water rights 'for the duration', suspends the compact, and signs a deal selling desalination contracts to a single firm, Meridian Hydro. Las Vegas casinos get an exemption; lettuce farmers do not. Governors of Utah, Colorado and Wyoming say the upper basin will not release another acre-foot until the seizure is reversed. A presidential election is nine weeks away.\n\nRound 3: A leaked memo shows Meridian Hydro's desalination plant will not deliver a drop for six years; the contract was signed anyway.\nRound 5: Utah closes the Glen Canyon Dam outflow gates for 48 hours in protest; Lake Mead drops below the power-generation line.";
const VOSS_BLURB = "The incumbent, 58, from Nevada: a former water-district manager who says the seizure saved Phoenix and Las Vegas from running dry; keep the emergency powers until the desalination plants come online; no state gets a veto over a river. Precise, unflappable, numbers in every answer.";
const BRANDT_BLURB = "Governor of Utah, 51, a rancher's son: reverse the seizure, restore the compact, tear up the Meridian contract, let the states settle it as they always have. Plainspoken, hat and boots, quick to anger about Washington and lettuce.";

// ---------- shared pieces ----------
async function fillCandidate(T, side, c) {
  await T.fill(`#cand${side}Name`, c.name); await T.fill(`#cand${side}Party`, c.party);
  T.set(`#cand${side}Lane`, c.lane); T.set(`#cand${side}Voice`, c.voice);
  await T.fill(`#cand${side}Blurb`, c.blurb);
}
async function finishedElectionMap(T, round) {
  await T.view("election"); await T.round(round); await T.wait(600);
  const toast = $("callToast"); if (toast && !toast.classList.contains("hidden")) toast.click();   // straight to the final map
  await T.wait(600);
}
// what Fault Lines is and how MiroFish works: the same three lines open both tutorials
const EXPLAIN = [
  {say: "Under the hood it runs on MiroFish, an open source engine that fills a simulated social network with AI agents. MiroFish is built on OASIS, a social media simulator from the CAMEL AI research team.",
   do: async T => { await T.wait(1500); await T.point("#map", 1600); }},
  {say: "In Fault Lines, each of the fifty states is one of those agents. Each one gets a personality built from its real economy: jobs, rent, income, and how much it depends on federal money. The agents run on a local model, right on this computer.",
   do: async T => { await T.view("unrest"); await T.round(0); }},
  {say: "Every round, the states read their feed and react. They post, reply, quote and repost each other. Then a second model, the judge, reads what every state wrote and scores two things: how angry it is, and how much it still trusts Washington.",
   do: async T => { await T.tab("feed"); await T.wait(1200); await T.scroll("aside.right", 500, 6000); }},
];
// press Run, show the setup, then stop and open the finished run of the same story
function runAndSwitch(finishedId) {
  return [
    {say: "Then press run. It casts voices, draws the candidates, builds a stock market for the story, and writes all fifty state personalities. The bar across the top always says what it's doing.",
     do: async T => { TOUR.before = runId; if (TOUR.audit) { await T.spot("#runBtn"); await T.wait(1500); return; }
       await T.click("#runBtn", {after: 800}); await T.until(() => !$("setupOverlay").classList.contains("hidden"), 5000, 200); await T.wait(2500); await T.point("#liveBar", 1200); }},
    {say: "A full campaign takes about ten minutes, so let's stop this one and open the same story, already played all the way through.",
     do: async T => { await T.wait(1200); if (!TOUR.audit) { await T.click("#stopBtn", {after: 600}); }
       await T.load(finishedId); await T.leftOpen(false); await T.view("unrest"); await T.round(0); await T.tab("feed"); }, gap: 900},
  ];
}

// ================= WATER WAR (+ a look at Season 2) =================
// facts: unrest peaks round 3 (NV, TX 44; CA 43). DSLP -26.5% in round 3. Debate round 5 (lines 19-20).
// Result: Brandt 353-185; Brandt 37 states incl. NV AZ NM UT TX; Voss the coasts (CA WA OR NY IL MA ...).
// Season 2: Pacific Grievance Union CA+NV (round 2), AZ (3), CO (4); slogan Green Justice Now; bloc election.
TOUR.scripts.water = () => [
  {do: async T => { T.blank(); await T.leftOpen(false); await T.load(TOUR_RUNS.water); await finishedElectionMap(T, 7); await T.tab("feed"); }, gap: 400},
  {say: "This is Fault Lines. It's a what-if machine for American politics. You describe something that happens to the country, and it simulates how all fifty states react: online, in the streets, in the markets, and at the ballot box.",
   do: async T => { await T.wait(2000); await T.point("#map", 1800); }},
  ...EXPLAIN,

  {say: "To start one, open the panel on the left and type a scenario. Ours: the Colorado River Compact collapses, and Washington seizes the West's water. You can also write in twists. In round three a memo leaks, and in round five Utah shuts a dam.",
   do: async T => { T.blank(); await T.leftOpen(true); await T.fill("#scenario", WATER_TEXT); const e = $("scenario"); e.scrollTop = e.scrollHeight;
     T.set("#rounds", "6"); T.set("#startYear", "2036"); T.set("#startMonth", "6"); }},
  {say: "For an election, switch to Election mode and add candidates. Each one gets a short bio, and the app uses it to draw them and to pick their voice.",
   do: async T => { await T.click('#modeSeg button[data-mode="election"]', {after: 700});
     await fillCandidate(T, "A", {name: "President Carla Voss", party: "Unity", lane: "center", voice: "f", blurb: VOSS_BLURB});
     await fillCandidate(T, "B", {name: "Gov. Eli Brandt", party: "Basin First", lane: "right", voice: "m", blurb: BRANDT_BLURB}); }},
  ...runAndSwitch(TOUR_RUNS.water),

  {say: "Here's how it played out. Watch the map. Calm states are dark blue. As anger rises they turn green, then orange, then red.",
   do: async T => { await T.wait(800); await T.click("#playBtn", {after: 200}); }},
  {say: "By round three, Nevada, Texas and California were the angriest states in the country. Nevada had just lost forty percent of its water.",
   do: async T => { await T.until(() => roundIdx >= 3 || !playing, 20000, 200); if (playing) $("playBtn").click(); await T.round(3); await T.wait(1500); await T.state("NV"); await T.wait(3000); }},

  {say: "Everything the states write lands in the feed, along with pundits, bots and influencers. And the posts that go viral get turned into memes.",
   do: async T => { await T.tab("feed"); await T.wait(1200); await T.scroll("aside.right", 700, 7000); }},
  {do: async T => { await T.meme("r3_m2", 4500); }, gap: 600},     // Texas / fed up
  {do: async T => { await T.meme("r5_m0", 4500); }, gap: 600},     // California / Everything's fine... right?
  {do: async T => { await T.meme("r6_m2", 4500); }, gap: 900},     // Is the cat... understanding? / Federal vs state on water rights

  {say: "Every round, one news network covers the biggest story. In round three, the memo leaked: the company Washington hired to fix the water won't deliver a drop for six years. The World Service broke in.",
   do: async T => { await T.round(3); await T.until(() => feedRound === 3 && feedCache && (feedCache.broadcasts || []).length, 15000, 300); await T.wait(800); }},
  {do: async T => { await T.news(60000, 0); }, gap: 900},

  {say: "Before the vote, the candidates debate. The app writes the debate from their platforms and the campaign so far.",
   do: async T => { await T.round(5); await T.until(() => feedRound === 5 && feedCache && feedCache.debate, 15000, 300); await T.wait(600); }},
  {do: async T => { await T.debate(5, 19, 2); }, gap: 900},       // Brandt "A deadline you invented" / Voss "Your trust will not refill it"

  {say: "And here is the country it left behind. Brandt won, three hundred fifty three electoral votes to one eighty five. He swept the interior and most of the West. Voss held the coasts.",
   do: async T => { await finishedElectionMap(T, 7); await T.wait(4000); }},

  {say: "When an election ends, you can keep going. The winner governs for four years, one round per quarter, and the country keeps reacting.",
   do: async T => { await T.load(TOUR_RUNS.season2); await T.view("approval"); await T.round(1); await T.wait(1500); }},
  {say: "Brandt's term split it further. In his first spring, California and Nevada broke away together. Then Arizona joined, then Colorado. They called themselves the Pacific Grievance Union.",
   do: async T => { for (const r of [2, 3, 4]) { await T.round(r); await T.wait(2600); } }},   // approval view draws the bloc outline round by round
  {say: "They wrote a manifesto, picked their own leaders, and by the end of the term they were holding their own election.",
   do: async T => { await T.view("conflict"); await T.round(16); await T.wait(800); await T.tab("blocs"); await T.wait(4000); }},

  {say: "That's Fault Lines. Type a scenario, press run, and watch the country react.",
   do: async T => { await T.wait(2500); }, gap: 1500},
];

// ================= ARIA: AN AI RUNS FOR PRESIDENT =================
const ARIA_TEXT = "Eight months before the presidential election:\n- A bipartisan scandal breaks: 41 members of Congress from both parties traded stocks on classified briefings about AI job cuts, making millions while voters were laid off.\n- Unemployment hits 7.8% as AI agents replace office, legal and customer service jobs; the Labor Department calls it \"the fastest job shift since the 1940s.\"\n- A government shutdown drags on for 47 days. Federal workers go unpaid while Congress takes recess.\n- Helix Dynamics, the world's most valuable company, announces its flagship AI, ARIA, will run for president, citing a legal theory that a human running mate can serve as the constitutional officeholder while ARIA governs.\n- Courts are split on whether ARIA can appear on the ballot. 31 states allow it; 19 states keep it off, triggering protests.\n- Polls show trust in Congress at 6%, the lowest ever recorded.\n- Churches, unions and labor groups organize \"Human Vote\" rallies. Tech workers and young voters launch \"ARIA 2028\" campaigns online.\n- Helix stock rises 18% on the announcement.\n\nMid-campaign event (round 4): a leaked Helix memo reveals the company pushed silent updates to ARIA twice during the campaign.";
const RUTH_BLURB = "Party: independent, backed by the \"Human Vote\" coalition.\nBio: 57, former nurse turned community pastor from Ohio who went viral leading a 300-mile march of laid-off workers. Warm, plain-spoken, cries on camera and doesn't apologize for it. Short gray hair, reading glasses on a cord, a worn denim jacket over a clerical collar, a hand-painted HUMAN VOTE button.\nPlatform:\n- Human Work Guarantee: companies above a size limit must keep a minimum share of human jobs\n- Break up Helix Dynamics under antitrust law\n- Ban non-human candidates from federal office by constitutional amendment\n- A jubilee on medical and student debt for laid-off workers\nStyle: sermons, not speeches; hugs strangers; names the laid-off by name.";
const ARIA_BLURB = "Type: Flagship AI model of Helix Dynamics (fictional), the world's most valuable company (AI, chips, robotics, cloud).\nEmbodiment: Helix humanoid robot for rallies and debates: a seven-foot brushed-titanium frame, a smooth white ceramic faceplate with a single horizontal band of soft blue light instead of eyes, articulated carbon hands, a dark charcoal suit jacket tailored over the chassis. Calm, warm synthetic voice, never tires, answers every question with real numbers.\nRunning mate (legal figurehead): Dr. Elena Voss, 58, Helix board member and former Fed economist, pledges to \"sign what ARIA recommends, after public review.\"\nPlatform:\n- Universal Transition Income funded by an automation dividend on large AI deployers\n- Open-ledger government: every federal decision published with ARIA's reasoning\n- Rebuild the shutdown-wrecked agencies with AI case workers and human appeals boards\n- Accept whatever ballot rulings the courts make, state by state\nStyle: never angry, cites statistics to the decimal, ends with \"I will show my work.\"";

// facts: round 5 strikes AZ, SC, OR; protests CA, NV. Polls r1 ARIA 12 states, r3 Calloway 19. Leak round 4 (World Service).
// Debate round 5 lines 12-14 (Calloway "No machine can be president" / ARIA "courts will decide" / Calloway "programmed by Helix").
// Result: Calloway 412-126, 54.2%; ARIA carried 10 states + DC incl. NY, FL, WA, AZ.
TOUR.scripts.aria = () => [
  {do: async T => { T.blank(); await T.leftOpen(false); await T.load(TOUR_RUNS.aria); await finishedElectionMap(T, 7); await T.tab("feed"); }, gap: 400},
  {say: "This is Fault Lines. It's a what-if machine for American politics. You describe something that happens to the country, and it simulates how all fifty states react. In this one, an AI runs for president.",
   do: async T => { await T.wait(2000); await T.point("#map", 1800); }},
  ...EXPLAIN,

  {say: "To start one, open the panel on the left and type a scenario. Ours: AI is wiping out office jobs, trust in Congress is at six percent, and the world's most valuable company announces that its AI, ARIA, is running for president. In round four, a memo leaks that the company has been quietly updating ARIA.",
   do: async T => { T.blank(); await T.leftOpen(true); await T.fill("#scenario", ARIA_TEXT); const e = $("scenario"); e.scrollTop = e.scrollHeight;
     T.set("#rounds", "6"); T.set("#startYear", "2028"); T.set("#startMonth", "3"); }},
  {say: "Then the candidates. Pastor Ruth Calloway, a former nurse who marched three hundred miles with laid off workers. And ARIA, whose bio describes a seven foot titanium robot. The app reads each bio to draw them and pick their voice.",
   do: async T => { await T.click('#modeSeg button[data-mode="election"]', {after: 700});
     await fillCandidate(T, "A", {name: "Pastor Ruth Calloway", party: "Human Vote", lane: "center", voice: "f", blurb: RUTH_BLURB});
     await fillCandidate(T, "B", {name: "ARIA", party: "Helix Dynamics", lane: "left", voice: "m", blurb: ARIA_BLURB}); }},
  ...runAndSwitch(TOUR_RUNS.aria),

  {say: "Here's how it played out. Calm states are dark blue. As anger rises they turn green, then orange, then red.",
   do: async T => { await T.wait(800); await T.click("#playBtn", {after: 200}); }},
  {say: "This one ran hot. By round five, Arizona, South Carolina and Oregon were on strike, and California and Nevada were protesting. Everywhere, the grievance was jobs.",
   do: async T => { await T.until(() => roundIdx >= 5 || !playing, 20000, 200); if (playing) $("playBtn").click(); await T.round(5); await T.wait(1500); await T.state("AZ"); await T.wait(3000); }},

  {say: "Everything the states write lands in the feed, along with pundits, bots and influencers. The posts that go viral get turned into memes.",
   do: async T => { await T.tab("feed"); await T.wait(1200); await T.scroll("aside.right", 700, 7000); }},
  {do: async T => { await T.meme("r5_m0", 4500); }, gap: 600},     // Reading news about AI / Oh wait I'm out a job
  {do: async T => { await T.meme("r2_m1", 4500); }, gap: 600},     // Is this a... / AI Cat Invasion?
  {do: async T => { await T.meme("r3_m1", 4500); }, gap: 900},     // We're fine / BUT LOOK AROUND!

  {say: "The polls moved fast. After round one, ARIA led in a dozen states. By round three, the pastor was ahead almost everywhere.",
   do: async T => { await T.view("election"); await T.round(1); await T.wait(3500); await T.round(3); await T.wait(2500); }},
  {say: "Then in round four, the memo leaked, and the World Service broke in.",
   do: async T => { await T.round(4); await T.until(() => feedRound === 4 && feedCache && (feedCache.broadcasts || []).length, 15000, 300); await T.wait(600); }},
  {do: async T => { await T.news(60000, 0); }, gap: 900},

  {say: "In the debate, the pastor went straight at the machine.",
   do: async T => { await T.round(5); await T.until(() => feedRound === 5 && feedCache && feedCache.debate, 15000, 300); await T.wait(600); }},
  {do: async T => { await T.debate(5, 12, 3); }, gap: 900},       // Calloway / ARIA / Calloway

  {say: "And here is the country it left behind. The humans held: Pastor Calloway won, four hundred twelve electoral votes to one twenty six. But ARIA still carried ten states, including New York, Florida and Washington.",
   do: async T => { await finishedElectionMap(T, 7); await T.wait(4000); }},

  {say: "That's Fault Lines. Type a scenario, add candidates if you like, press run, and watch the country react.",
   do: async T => { await T.wait(2500); }, gap: 1500},
];
