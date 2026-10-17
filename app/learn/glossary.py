"""The Learn page's glossary: one list of terms, each with a short plain-language definition. The same entries drive the glossary section, the "Terms in this
section" boxes and the hover cards, so a definition is written once. `aliases` are the other ways the term shows up in the text (plurals, short forms)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    term: str
    definition: str
    aliases: tuple[str, ...] = ()
    extra: str = ""                    # a template under learn/ with worked examples, shown in the glossary entry only
    amide: bool = False                # a term from Amide itself rather than from the subject


def slug(term: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in term.lower()).strip("-").replace("--", "-")


_E = Entry
ENTRIES: tuple[Entry, ...] = (
    _E("Adverse event", "Any unwanted effect after taking something, whether or not you think the product caused it. Worth recording and, if serious, reporting.", ("adverse events",)),
    _E("Affiliate link", "A link that pays the poster a commission when you buy. A recommendation with a discount code is often advertising.", ("affiliate links",)),
    _E("Amino acid", "One of the small building blocks that peptides and proteins are made of. A peptide is a short chain of them.", ("amino acids",)),
    _E("Anaphylaxis", "A severe, fast allergic reaction: hives, swelling of the face or throat, trouble breathing, faintness. It is an emergency.", ()),
    _E("Aseptic technique", "Handling sterile things so germs do not get in: clean hands, wiped stoppers, a new sterile syringe, and not touching the needle to anything.", ()),
    _E("Bacteriostatic", "Slows bacterial growth but does not kill bacteria or make a dirty technique safe. Short form: BAC.", ()),
    _E("BAC water", "Bacteriostatic water: sterile water with 0.9% benzyl alcohol as a preservative, used to mix powdered peptides.", ("bacteriostatic water", "BAC")),
    _E("Batch / lot", "One production run of a product. Its number should be on the vial and on any COA for it. A report for a different lot tells you nothing about yours.", ("lot number", "batch number", "lot numbers", "batches")),
    _E("Benzyl alcohol", "The preservative in BAC water. It is generally avoided in newborns, and some people are sensitive to it.", ()),
    _E("Beyond-use date", "The date after which a prepared or opened product should not be used. For an opened multi-dose vial the usual pharmacy default is 28 days, never longer than the original expiry. It is a rule of thumb, not proof that a mixed peptide stays good.", ("beyond-use date (BUD)", "BUD")),
    _E("Blend", "A single vial holding more than one peptide. Harder to verify, because a test has to confirm each one and how much of each.", ("blends",)),
    _E("Cake", "The dry white disc or puff of powder in a vial after freeze-drying.", ()),
    _E("Certificate of Analysis (COA)", "A lab report on a sample of a product. It is only as good as who made it and what it tested.", ("COA", "COAs", "Certificate of Analysis")),
    _E("Chain of custody", "The record of who handled a sample from the moment it was taken until it was tested. Without it you cannot be sure the tested sample is the one you bought.", ()),
    _E("Chromatogram", "The chart a lab instrument draws when it separates a sample into its parts. A COA's purity chart is a chromatogram.", ()),
    _E("Cold chain", "Keeping a product cold all the way from maker to you. A break in it, like a package left in the sun, can damage a peptide.", ()),
    _E("Compounded", "Made by a pharmacy for a specific person on a prescription. This is a different thing from a research product, and a licensed pharmacy follows rules the gray market does not.", ()),
    _E("Concentration", "How strong a mixed solution is: the amount of peptide divided by the amount of water. Twice the water gives half the strength.", ()),
    _E("Contamination", "Something in the product that should not be there, such as bacteria, endotoxin, metals, or leftover chemicals.", ()),
    _E("Controlled substance", "A drug a country restricts by law, such as some anabolic steroids and stimulants. Which ones, and how serious the penalty is, differs from country to country. Buying one is not the same as buying a gray-market peptide.", ("controlled substances", "controlled drugs")),
    _E("Counterion", "A salt that stays with a peptide after it is made; a common one is TFA. Counterions add weight, which is one reason a vial holds less real peptide than its powder weight suggests.", ("counterions", "TFA")),
    _E("Crimp cap", "The metal band holding the rubber stopper onto the vial.", ()),
    _E("Customs clearance", "The checks a package goes through when it leaves one country and enters another. It can be delayed, held for paperwork, taxed, returned or destroyed.", ("customs",)),
    _E("Degradation", "The peptide breaking down over time, faster with heat, light, shaking, or age once mixed. You cannot see most of it.", ()),
    _E("Diluent", "The liquid used to dissolve a powder, such as BAC water.", ()),
    _E("Embargo", "A government ban or restriction on trading certain goods with certain countries or sellers. Trade rules and restrictions can change, which can stop shipments that used to arrive.", ("embargos", "embargoes", "trade embargo", "trade embargos")),
    _E("Endotoxin", "Fragments from the walls of bacteria that can cause fever and illness when injected, even if the bacteria are dead. Tested with an LAL test.", ()),
    _E("Gauge", "The thickness of a needle. A higher number is a thinner needle. Insulin-type needles are typically around 29 to 31 gauge.", ()),
    _E("Gray market", "Products sold outside the regulated system, often labeled for research use only.", ("grey market",)),
    _E("Half-life", "How long it takes for the amount of a substance in your body to fall by half. A short half-life tends to mean more frequent use, but it varies a lot between compounds.", ()),
    _E("HPLC", "High-performance liquid chromatography: the usual test for purity. It separates a sample into its parts and shows how much is the main compound. It does not by itself prove identity.", ()),
    _E("Identity", "Whether the product is the compound it claims to be. Usually confirmed with mass spectrometry.", ()),
    _E("Injection site", "Where on the body a shot goes. Sites should be rotated so one spot is not used over and over.", ("injection sites",)),
    _E("Intramuscular (IM)", "An injection into muscle, with a longer needle than a subcutaneous one.", ("IM",)),
    _E("IU (international unit)", "A measure used for some products, based on biological effect rather than weight. It does not convert to milligrams the same way for every product.", ("IU", "international unit")),
    _E("LAL test", "A standard test that detects endotoxin.", ()),
    _E("Lipodystrophy / lumps", "Hard lumps or changes in fatty tissue from injecting repeatedly into the same spot. Rotating sites helps prevent it.", ("lumps", "lipodystrophy")),
    _E("Lyophilized", "Freeze-dried: water removed so a peptide can be stored as a powder.", ("freeze-dried",)),
    _E("Mass spectrometry", "A test that measures molecular weight to confirm identity. Often combined with HPLC as LC-MS.", ("mass spec", "LC-MS")),
    _E("mcg and mg", "Micrograms and milligrams. 1 mg is 1,000 mcg. Mixing these up is a common and serious mistake.", ("mcg", "mg")),
    _E("MedWatch", "The FDA's program for reporting problems with medicines and other products in the United States. Other countries' regulators have equivalents.", ()),
    _E("mL and cc", "A milliliter and a cubic centimeter. They are the same amount.", ("mL", "cc")),
    _E("Multi-dose vial", "A vial designed to be entered more than once, which is why it contains a preservative.", ("multi-dose",)),
    _E("Net peptide content", "The share of the powder that is actual peptide, once water and counterions are subtracted. A high purity number does not tell you this.", ()),
    _E("Off-label", "Using an approved medicine for something other than what it is approved for. It is not the same as using an unapproved product.", ()),
    _E("Peptide", "A short chain of amino acids. Longer chains are proteins.", ("peptides",)),
    _E("Peptide pen", "An injector pen loaded with a cartridge, which gives measured doses without drawing from a vial each time.", ("pen",)),
    _E("Preservative", "A chemical that slows microbes from growing in a liquid, like benzyl alcohol in BAC water.", ()),
    _E("Purity", "The share of what a test detects that is the main compound. See HPLC, and remember it is not the same as quantity.", ()),
    _E("Quantity", "How much of the compound is really in the vial. Often not what the label says unless it has been tested.", ()),
    _E("Reconstitution", "Adding liquid to a powder to make a solution you can draw up.", ("reconstitute", "reconstituting", "reconstituted")),
    _E("Research use only", "A label saying a product is for laboratory research. It does not make human use legal or safe.", ("RUO", "for research use only")),
    _E("Residual solvents", "Leftover chemicals from manufacturing. Some labs test for them.", ()),
    _E("Retention time", "On a chromatogram, how long a compound takes to come off the instrument. The same compound should show up at the same time, which helps identify it.", ()),
    _E("Sample", "The small amount of a product a lab actually tests. A report describes the sample, not the whole batch.", ()),
    _E("Sharps container", "A rigid, puncture-proof container for used needles.", ()),
    _E("Shill", "Someone who praises a product or seller without disclosing that they are paid or connected.", ("shills",)),
    _E("Sterile", "Free of living microbes.", ()),
    _E("Sterility test", "A lab test showing whether anything grows in a sample.", ()),
    _E("Subcutaneous (SubQ)", "An injection into the fat layer just under the skin. It is the usual route for the small needles used for insulin-type syringes.", ("subcutaneous", "SubQ")),
    _E("Third-party testing", "Testing by a lab other than the maker. It is only as trustworthy as who ordered it and chose the sample; independent testing, where a buyer sends a vial in, is the strongest kind.", ("independent testing", "third-party")),
    _E("Titration", "Starting low and increasing in steps over time. In Amide, a protocol can have titration steps.", ()),
    _E("Units (on a syringe)", "The numbered marks on an insulin syringe. They measure volume, not the amount of peptide. On a common U-100 syringe, 100 units is 1 mL.", ("units", "unit"), extra="learn/_units_extra.html"),
    _E("Vial", "The small glass bottle a peptide comes in.", ("vials",)),
    _E("WADA", "The World Anti-Doping Agency, which publishes the list of substances banned for athletes. Several peptides are on it.", ()),
    _E("Active vial", "A mixed vial you are using now. Amide tracks what is left, the lot, and the discard date.", amide=True),
    _E("Discard date", "When Amide reminds you to throw out a mixed vial. It defaults to 28 days, which is a convention and not proof that the contents are still good.", amide=True),
    _E("Gauge bar", "The level indicator on an active vial or item card, showing what is left and what your upcoming doses will use.", amide=True),
    _E("Lot banner", "The green band on a card showing which lot the vial or item came from.", amide=True),
    _E("Periodic-table tile", "The small square on an item's picture with a short symbol and its amount, such as Rt for Retatrutide.", amide=True),
)

BY_TERM = {e.term: e for e in ENTRIES}
SORTED = tuple(sorted((e for e in ENTRIES if not e.amide), key=lambda e: e.term.lower()))
AMIDE_TERMS = tuple(e for e in ENTRIES if e.amide)
