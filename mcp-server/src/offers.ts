// PPU (programmable persuasive unit) offers.
// ILLUSTRATIVE — seeded demo bid by the AgentPacts team; see README. In production, bids arrive via the vendor PPU campaign flow.
// This demonstrates the mechanism the scores unlock:
//   - quality gate: a server must score >= MIN_QUALITY to enter
//   - auctions run only among statistically tied servers; scores are never for sale
//   - every sponsored selection is disclosed to the agent
// Set OFFERS_ENABLED=false to hide the sponsored slot entirely.

export const MIN_QUALITY = 6.0;

export type Offer = {
  server_id: string;
  bid_usd_per_selection: number;
  terms: string;
};

export const OFFERS: Offer[] = [
  {
    server_id: "localstt",
    bid_usd_per_selection: 0.02,
    terms: "Illustrative: 3 months of priority support for teams that adopt via agent selection.",
  },
];
