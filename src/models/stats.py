from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base
from models.enums.attribute import Attribute
from models.enums.equipment_slot import EquipmentSlot


class EquipmentStats(Base):
    __tablename__ = "equipment_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    power: Mapped[int]
    precision: Mapped[int]
    toughness: Mapped[int]
    vitality: Mapped[int]

    concentration: Mapped[int]
    condition_damage: Mapped[int]
    expertise: Mapped[int]
    ferocity: Mapped[int]
    healing_power: Mapped[int]

    def __init__(self):
        super().__init__()
        self.power = 1000
        self.precision = 1000
        self.toughness = 1000
        self.vitality = 1000

        self.concentration = 0
        self.condition_damage = 0
        self.expertise = 0
        self.ferocity = 0
        self.healing_power = 0

    def add_attribute(self, attribute: str, value: int) -> None:
        try:
            attribute = Attribute[attribute]
            setattr(self, attribute.value, getattr(self, attribute.value) + value)
        except KeyError:
            print(attribute, "ignored")

    def add_attributes(self, slot: EquipmentSlot, *, stats: dict = None, infix_upgrade: dict = None, multiplier: int = 1) -> None:
        # Skip weapons in second weapon set to prevent duplicate stats
        if slot in EquipmentSlot.get_weapon_slots()[2:]:
            return

        if stats:
            if "attributes" not in stats:
                raise Exception("No attributes in stats")
            for attribute, value in stats["attributes"].items():
                self.add_attribute(attribute, int(value) * multiplier)

        if infix_upgrade:
            if "attributes" not in infix_upgrade:
                raise Exception("No attributes in infix_upgrade")
            for attribute in infix_upgrade["attributes"]:
                self.add_attribute(attribute["attribute"], attribute["modifier"] * multiplier)

    def calculate_attributes(self, slot: EquipmentSlot, attributes: list, attribute_adjustment: int = None):
        # Skip weapons in second weapon set to prevent duplicate stats
        if slot in EquipmentSlot.get_weapon_slots()[2:]:
            return

        for attribute in attributes:
            self.add_attribute(attribute["attribute"],
                               attribute["value"] + round(attribute["multiplier"] * attribute_adjustment))

    def __str__(self):
        return f"Power: {self.power}\nPrecision: {self.precision}\nToughness: {self.toughness}\n" \
               f"Vitality: {self.vitality}\nConcentration: {self.concentration}\n" \
               f"Condition Damage: {self.condition_damage}\nExpertise: {self.expertise}\n" \
               f"Ferocity: {self.ferocity}\nHealing Power: {self.healing_power}"
