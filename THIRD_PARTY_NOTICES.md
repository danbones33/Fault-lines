# Third-party notices

Fault Lines is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`). It includes or depends on the
following third-party software.

## Included in this repository (frontend/vendor)

| Component | Version | License | Copyright |
|---|---|---|---|
| [d3](https://d3js.org) (`d3.v7.min.js`) | 7.9.0 | ISC | Copyright 2010-2023 Mike Bostock |
| [topojson-client](https://github.com/topojson/topojson-client) (`topojson-client.min.js`) | 3.1.0 | ISC | Copyright 2019 Mike Bostock |
| [us-atlas](https://github.com/topojson/us-atlas) (`us-states-10m.json`) | 3.x | ISC (data from the U.S. Census Bureau's cartographic boundary files, public domain) | Copyright 2013-2019 Michael Bostock |

The vendored file headers carry the version and copyright lines above; they do not name the license. The licenses
listed are the ones each project publishes in its repository. The ISC license text:

> Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
> granted, provided that the above copyright notice and this permission notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
> INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
> AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
> PERFORMANCE OF THIS SOFTWARE.

## Required at run time (installed separately, not included)

| Component | License | How it is used |
|---|---|---|
| [MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline), a fork of [MiroFish](https://github.com/666ghj/MiroFish) | AGPL-3.0 | `sim/runner.py` imports and subclasses MiroFish's Twitter simulation runner. This is why Fault Lines is AGPL-3.0. Cloned next to this repo by the setup steps; not vendored. |
| [OASIS](https://github.com/camel-ai/oasis) (`camel-oasis`) by CAMEL-AI | Apache-2.0 | The social platform engine under MiroFish. Installed in MiroFish's environment. |
| [Laya](https://huggingface.co/convaiinnovations/laya) (`laya`) by Convai Innovations | Apache-2.0 | Optional: typed agent decisions in one forward pass. Installed in MiroFish's environment. |
| Python packages in `pyproject.toml` / `uv.lock` (FastAPI, Uvicorn, httpx, PyYAML, Pydantic, Pillow) | MIT / BSD / HPND | The web server and helpers. |

## Services (optional, your own keys)

Ollama runs the local model. OpenRouter, Cartesia, Fish Audio and fal.ai are optional paid services used only when you
put their keys in `.env`; their terms apply to what you generate with them.
