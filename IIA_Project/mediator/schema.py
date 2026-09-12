"""
Global Virtual Schema Definition (VEHICLE_PROFILE).
Never stored in a database; dynamically constructed by the federated mediator.
"""

from typing import Dict, Any, List

GLOBAL_SCHEMA_ATTRIBUTES: Dict[str, Dict[str, Any]] = {
    "plate_number": {
        "type": "string",
        "description": "Canonical vehicle license plate number (alphanumerics uppercase)",
        "example": "DL01AB1234",
        "category": "identifier",
        "is_key": True
    },
    # Registration attributes
    "owner_name": {
        "type": "string",
        "description": "Full name of the registered vehicle owner",
        "example": "Ramesh Sharma",
        "category": "registration",
        "primary_source": "REG"
    },
    "vehicle_make": {
        "type": "string",
        "description": "Manufacturer / brand of the vehicle",
        "example": "Hyundai",
        "category": "registration",
        "primary_source": "REG"
    },
    "vehicle_model": {
        "type": "string",
        "description": "Model name of the vehicle",
        "example": "Creta",
        "category": "registration",
        "primary_source": "REG"
    },
    "vehicle_colour": {
        "type": "string",
        "description": "Official registered colour of the vehicle",
        "example": "White",
        "category": "registration",
        "primary_source": "REG"
    },
    "registration_date": {
        "type": "date",
        "description": "Date when the vehicle was officially registered (YYYY-MM-DD)",
        "example": "2023-01-15",
        "category": "registration",
        "primary_source": "REG"
    },
    "registration_status": {
        "type": "string",
        "description": "Status of registration: ACTIVE, SUSPENDED, or CANCELLED",
        "example": "ACTIVE",
        "category": "registration",
        "primary_source": "REG"
    },
    # Insurance attributes
    "insurer_name": {
        "type": "string",
        "description": "Name of the insurance underwriter company",
        "example": "National Shield Insurance",
        "category": "insurance",
        "primary_source": "INS"
    },
    "policy_type": {
        "type": "string",
        "description": "Policy coverage type (THIRD_PARTY or COMPREHENSIVE)",
        "example": "COMPREHENSIVE",
        "category": "insurance",
        "primary_source": "INS"
    },
    "insurance_start": {
        "type": "date",
        "description": "Start date of the active policy (YYYY-MM-DD)",
        "example": "2026-01-15",
        "category": "insurance",
        "primary_source": "INS"
    },
    "insurance_expiry": {
        "type": "date",
        "description": "Expiration date of the policy (YYYY-MM-DD)",
        "example": "2027-01-14",
        "category": "insurance",
        "primary_source": "INS"
    },
    "insurance_status": {
        "type": "string",
        "description": "Derived insurance status: VALID, EXPIRED, NONE, or UNKNOWN",
        "example": "VALID",
        "category": "insurance",
        "is_derived": True
    },
    # Theft attributes
    "stolen_status": {
        "type": "string",
        "description": "Derived stolen status: STOLEN, RECOVERED, NOT_REPORTED, or UNKNOWN",
        "example": "NOT_REPORTED",
        "category": "theft",
        "is_derived": True
    },
    "last_incident_date": {
        "type": "date",
        "description": "Date of latest reported crime/incident",
        "example": "2026-08-15",
        "category": "theft",
        "primary_source": "THEFT"
    },
    "case_status": {
        "type": "string",
        "description": "Status of police incident case: OPEN or CLOSED",
        "example": "OPEN",
        "category": "theft",
        "primary_source": "THEFT"
    },
    # Sighting attributes
    "last_seen_location": {
        "type": "string",
        "description": "Location name where vehicle was last photographed",
        "example": "NH8 Toll Plaza",
        "category": "camera",
        "primary_source": "CAM"
    },
    "last_seen_time": {
        "type": "datetime",
        "description": "Timestamp of most recent camera capture",
        "example": "2026-09-04T08:30:00",
        "category": "camera",
        "primary_source": "CAM"
    },
    "observed_make": {
        "type": "string",
        "description": "Vehicle make observed by roadside camera",
        "example": "Hyundai",
        "category": "camera",
        "primary_source": "CAM"
    },
    "observed_model": {
        "type": "string",
        "description": "Vehicle model observed by roadside camera",
        "example": "Creta",
        "category": "camera",
        "primary_source": "CAM"
    },
    "observed_colour": {
        "type": "string",
        "description": "Vehicle colour observed by roadside camera",
        "example": "White",
        "category": "camera",
        "primary_source": "CAM"
    },
    # Extensibility attribute (PUC - UC6)
    "puc_expiry": {
        "type": "date",
        "description": "Validity expiry date of pollution certificate",
        "example": "2027-01-10",
        "category": "puc",
        "primary_source": "PUC"
    }
}
