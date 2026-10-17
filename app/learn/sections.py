"""The Learn tutorial's sections, in order. Each section's body is a template under templates/learn/; `terms` are the glossary terms the section uses, which are
listed in its "Terms in this section" box and underlined (first use only) with a hover card."""

from dataclasses import dataclass

LAST_UPDATED = "October 17, 2026"


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    terms: tuple[str, ...]

    @property
    def template(self) -> str:
        return f"learn/{self.slug}.html"


SECTIONS: tuple[Section, ...] = (
    Section("s01", "Welcome to the Wonderful World of Maybe?", ("Gray market", "Research use only", "Peptide")),
    Section("s02", "Risks vs Rewards", ("Contamination", "Endotoxin")),
    Section("s03", "Break Out Yer 6-Guns, Welcome to the Wild Wild West", ("Gray market", "Customs clearance", "Embargo", "Controlled substance")),
    Section("s04", "How to locate gray market vendors", ("Affiliate link", "Third-party testing")),
    Section("s05", "How to vet gray market vendors", ("Batch / lot", "Third-party testing")),
    Section("s06", "What is a COA, and why the type matters?", ("Certificate of Analysis (COA)", "HPLC", "Mass spectrometry", "Purity", "Identity", "Quantity", "Net peptide content", "Endotoxin")),
    Section("s07", "How to read a COA", ("Sample", "Batch / lot")),
    Section("s08", "What are these little bottles?", ("Vial", "Lyophilized", "Cake", "Multi-dose vial")),
    Section("s09", "You want me to put water off my back where?", ("Bacteriostatic", "BAC water", "Benzyl alcohol", "Preservative", "Sterile")),
    Section("s10", "You want me to stab myself, sometimes multiple times per day?", ("Sharps container", "Units (on a syringe)")),
    Section("s11", "If you react: what to do", ("MedWatch", "Batch / lot")),
    Section("s12", "Is this legal where I live?", ("Research use only", "Compounded", "WADA")),
)
