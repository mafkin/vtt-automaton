# Comics: known issues

What we learned testing comic generation on the E3 transcript (Tyrmiä ja turpakäräjiä E3), October 2026. The test comic is `tyrmia-e3-20261006-1256-8c39` in `data/comic/comics/`: three pages, four drawing rounds, compared side by side in the dashboard.

## What works
- **Script from the transcript:** extracting events, suggesting moments, and a reviewed script. The story is coherent and the Finnish dialogue is good, even from a noisy transcript without speaker tags.
- **Lettering:** Finnish text with ä/ö is lettered correctly. The lettering check catches missing, doubled or extra text.
- **Character sheets:** characters stay recognisable across pages, especially the distinctive ones (Rintaro the seal, Käl, Oakrend).
- **Continuity:** drawing each page with the previous one keeps the rendering style consistent within a comic.
- **The look check is accurate:** every problem it reported was real when checked by eye.

## Known issues

### Small details don't stay identical
Insignia and small decoration change from page to page: Pentik's helm decoration (a cross appears or disappears), the crest on his breastplate and the emblem on his shield. They kept changing with all of these in place:
- a character sheet and a detail sheet;
- exact must-haves and a never list;
- continuity with the previous page.

The image model redraws characters in every panel rather than copying them. **Realistic target: recognisable characters (silhouette, colours, weapon type), not identical insignia.**

### Automatic redraws multiply the cost and rarely fix the problem
With the look check on and one automatic redraw, most pages were drawn twice. The second attempt often repeated the same mistake, because the same inputs produced the same result. A page then costs about 15,000 tokens instead of 6,000–8,000.
- Since `fed148d`, a redraw is told what the check found, which should help. It wasn't tested before testing stopped.
- **Recommended:** set automatic redraws to 0 (Limits card), or turn the look check off, and redraw bad pages by hand with an instruction.

### Contradicting inputs win over the spec
Since the prompt-consistency change, every page prompt states one order of authority: character
rules and sizes, then reference images, then panel descriptions, then style and previous page.
The previous page no longer tells the drawer to keep characters "exactly as there", which used
to carry a wrong drawing forward to every later page. Character sheets keep each character's
natural stance and proportions (short legs stay short) instead of a generic "neutral pose".
The drawer follows whatever it's given, and when inputs disagree the result is unpredictable:
- The script said Pentik points "his sword" (miekka) while his spec said flail → he got a sword. Fixed: the script writer now gets must-haves and never lists, but **check older scripts by hand**.
- A style reference page that shows an old version of a character brings that version back (Pentik's helm cross). **Only use a page as the style reference if every character on it is correct.**
- A drawn sheet can add details your reference doesn't have (a cross on the helm; a shield Jessan doesn't carry). **Check sheets against your reference before approving.**

### Speaker placement and extras
- A balloon can sit next to the wrong character in crowded panels.
- Unnamed extras can look like a party member (a dark-haired assassin read as Jessan).

Speaker tags (`/link`) and fewer characters per panel help. Since the reference-pipeline change, each panel lists the characters in it with their reference image numbers, balloons name the speaker as the references do (an alias becomes the name), and anyone a panel doesn't list is an unnamed extra who must not look like a party member. Not tested on the server yet.

### Typos in the script look like lettering errors
The drawer corrects a misspelling in the script ("Väistykaa" → "Väistykää"), and the lettering check then reports the balloon as missing. Fix the script text, or ignore that report.

### Gemini overload
`gemini-3-pro-image` regularly answers **503 UNAVAILABLE ("high demand")**.
- Requests are retried four times with backoff. Failed requests cost nothing.
- During longer overloads a step still ends as "failed": draw again later.

### What reaches the page drawer (reference-pipeline change)
Before testing again, these gaps were fixed on the application side:
- **Your own images reach pages.** Before, with an approved sheet, pages saw only the AI-drawn
  sheet, so a sheet's drift (an extra helm cross, a taller Rintaro) carried into every page.
  Pages now also get the first uploaded image ("on pages" on the Bible card; **⇤ first** picks
  which). Without a sheet, pages can get up to three of your images. Limits card: 0-3, default 1.
- **Labels sit next to their images.** Images used to go first and the text called them
  "reference image 3"; each image now follows its own label, so the model ties names to the
  right picture (sheets and detail sheets too).
- **Everyone on the page gets references.** The cast came only from the page's character list;
  a character who only speaks or is only named in a panel got no references, rules or size.
  Characters without images are still described by their rules.
- **2K output.** Pages and sheets are drawn at 2K (same tokens as 1K for `gemini-3-pro-image`),
  so small details have twice the pixels. `GEMINI_IMAGE_SIZE` in `comic/.env` changes it
  (empty: the model default, for models without the setting).
- **Inputs are sent at 1536 px as JPEG.** A request must stay under 20 MB in total; full-size
  PNG sheets and pages for a cast of four, drawn at 2K, could pass it.
- **Uploads:** a transparent background (token art) became black, hiding a dark character's
  outline; it is now white. Phone photos are turned upright. **Re-upload any transparent or
  sideways images uploaded before this change.**

## Costs measured
| What | Tokens |
|---|---|
| Reading a full session transcript (events) | ~35,000 (text, not budgeted) |
| Writing the script | ~4,000–6,000 (text, not budgeted) |
| One page with inspection, no redraw | ~6,000–8,000 |
| One page with one automatic redraw | ~15,000 |
| Character sheet / detail sheet | ~5,000–6,000 each (bible, not budgeted) |

The drawing budget is per comic (Limits card, default 80,000). It covers page images and their checks, and drawing stops before a page that would go over it. The whole test (three pages, four rounds plus sheets) used about 145,000 drawing tokens.

## Recommendations
1. Set every character's **height** in the bible (cm). Reference images aren't to scale, so
   each page and character sheet states the relative sizes of exactly the characters in it,
   compared with the tallest one present (e.g. "the top of RINTARO's head reaches PENTIK's
   knees"). Characters without a height are left out of the comparison.
2. Make each character's best full-body image the first one (**⇤ first**): it goes with
   every page. Check approved sheets against it; delete a sheet that drifted.
3. Aim for recognisable characters. Keep must-haves to big visible features; put exact insignia only where they matter.
4. One draw per page (automatic redraws 0); redraw the few bad pages by hand with an instruction.
5. Fewer characters per panel, and close-ups when a detail matters to the joke.
6. Use speaker tags in sessions and check the ✓/✗ list in the transcript preview before starting a comic.
7. Choose the style reference from a page where everyone is drawn right.
8. Not tried yet: other image models. A fair comparison needs the same script, sheets and style reference, one round per model. `gemini-3.1-flash-image` (faster, cheaper) is available with the current key. If a model refuses the output size, set `GEMINI_IMAGE_SIZE=` (empty) in `comic/.env`.
