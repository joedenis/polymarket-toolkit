"""List the busiest open Polymarket markets, to pick a slug to record or capture."""

from itertools import islice

from pmkit.gamma import GammaClient

N = 15

gamma = GammaClient()
markets = gamma.iter_markets(
    closed=False,
    page_size=N,
    order="volume24hr",   # busiest in the last 24h first
    ascending=False,
)

for m in islice(markets, N):
    yes = m.outcomes[0].price if m.outcomes else None
    print(f"{m.slug:70}  yes={yes}  {m.question}")