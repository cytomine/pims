#  * Copyright (c) 2020-2021. Authors: see NOTICE file.
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      http://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
import logging
from typing import Optional

from cytomine.models import Model
from pint import Quantity

log = logging.getLogger("pims")


def response_list(list_):
    """Format a list for response serialization.
    """
    return {
        "items": list_,
        "size": len(list_)
    }


def convert_quantity(quantity: Optional[Quantity], unit: str, ndigits: int = 6) -> Optional[float]:
    """
    Convert a quantity to the unit required by API specification.

    Parameters
    ----------
    quantity
        Quantity to convert
    unit
        Pint understandable unit
    ndigits
        Number of digits to keep for rounding

    Returns
    -------
    float
        Converted quantity to given unit if `quantity` is Quantity
    """
    if quantity is None:
        return None
    elif isinstance(quantity, Quantity):
        return round(quantity.to(unit).magnitude, ndigits)

    log.warning(
        f'The quantity {quantity} is not of type Quantity and is thus not converted.'
    )
    return round(quantity, ndigits)


def serialize_cytomine_model(o):
    if isinstance(o, Model):
        d = dict((k, v) for k, v in o.__dict__.items() if v is not None and not k.startswith("_"))
        if "uri_" in d:
            d["uri"] = d.pop("uri_")
        return d
    log.warning(f"The object {o} is not a Cytomine model and is thus not serialized.")
    return o
