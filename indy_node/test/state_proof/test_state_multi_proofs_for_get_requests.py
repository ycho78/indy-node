import base64
import copy
import random

import time

import base58
import pytest
from common.serializers import serialization
from common.serializers.serialization import state_roots_serializer, domain_state_serializer
from crypto.bls.bls_multi_signature import MultiSignature, MultiSignatureValue
from indy_common.authorize.auth_constraints import ConstraintsSerializer
from indy_common.authorize.auth_map import auth_map
from indy_common.authorize.auth_request_validator import WriteRequestValidator
from plenum.bls.bls_store import BlsStore
from plenum.common.constants import TXN_TYPE, TARGET_NYM, RAW, DATA, \
    IDENTIFIER, NAME, VERSION, ROLE, VERKEY, KeyValueStorageType, \
    STATE_PROOF, ROOT_HASH, MULTI_SIGNATURE, PROOF_NODES, TXN_TIME, CURRENT_PROTOCOL_VERSION, DOMAIN_LEDGER_ID, NYM
from plenum.common.txn_util import reqToTxn, append_txn_metadata, append_payload_metadata, set_type
from plenum.common.types import f
from indy_common.constants import \
    ATTRIB, CLAIM_DEF, SCHEMA, CLAIM_DEF_FROM, CLAIM_DEF_SCHEMA_REF, CLAIM_DEF_SIGNATURE_TYPE, \
    CLAIM_DEF_PUBLIC_KEYS, CLAIM_DEF_TAG, SCHEMA_NAME, SCHEMA_VERSION, SCHEMA_ATTR_NAMES, LOCAL_AUTH_POLICY, \
    CONFIG_LEDGER_AUTH_POLICY, GET_NYM, GET_ATTR, GET_CLAIM_DEF, GET_SCHEMA, CONTEXT_NAME, CONTEXT_VERSION, GET_CONTEXT, \
    CONTEXT_CONTEXT, META, CONTEXT_TYPE, RS_TYPE, SET_CONTEXT
from indy_common.types import Request
from indy_node.persistence.attribute_store import AttributeStore
from indy_node.persistence.idr_cache import IdrCache
from plenum.common.util import get_utc_epoch, friendlyToRaw, rawToFriendly, \
    friendlyToHex, hexToFriendly
from plenum.server.request_handlers.utils import nym_to_state_key
from plenum.test.testing_utils import FakeSomething
from state.pruning_state import PruningState
from storage.kv_in_memory import KeyValueStorageInMemory
from indy_common.state import domain


def extract_proof(result, expected_multi_sig):
    proof = result[STATE_PROOF]
    assert proof
    assert proof[ROOT_HASH]
    assert proof[PROOF_NODES]
    multi_sign = proof[MULTI_SIGNATURE]
    assert multi_sign
    assert multi_sign == expected_multi_sig
    return proof


def save_multi_sig(db_manager):
    multi_sig_value = MultiSignatureValue(ledger_id=DOMAIN_LEDGER_ID,
                                          state_root_hash=state_roots_serializer.serialize(
                                              bytes(db_manager.get_state(DOMAIN_LEDGER_ID).committedHeadHash)),
                                          txn_root_hash='2' * 32,
                                          pool_state_root_hash='1' * 32,
                                          timestamp=get_utc_epoch())
    multi_sig = MultiSignature('0' * 32, ['Alpha', 'Beta', 'Gamma'], multi_sig_value)
    db_manager.bls_store.put(multi_sig)
    return multi_sig.as_dict()


def is_proof_verified(db_manager,
                      proof, path,
                      value, seq_no, txn_time):
    encoded_value = domain.encode_state_value(value, seq_no, txn_time)
    proof_nodes = base64.b64decode(proof[PROOF_NODES])
    root_hash = base58.b58decode(proof[ROOT_HASH])
    verified = db_manager.get_state(DOMAIN_LEDGER_ID).verify_state_proof(
        root_hash,
        path,
        encoded_value,
        proof_nodes,
        serialized=True
    )
    return verified


def test_state_proofs_for_get_attr(write_manager,
                                   read_manager,
                                   db_manager):
    # Adding attribute
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'
    attr_key = 'last_name'
    raw_attribute = '{"last_name":"Anderson"}'
    seq_no = 0
    txn_time = int(time.time())
    identifier = "6ouriXMZkLeHsuXrN1X1fd"
    txn = {
        TXN_TYPE: ATTRIB,
        TARGET_NYM: nym,
        RAW: raw_attribute,
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=txn,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)

    # Getting attribute
    get_request = Request(
        operation={
            TARGET_NYM: nym,
            RAW: 'last_name',
            TXN_TYPE: GET_ATTR,
        },
        signatures={},
        protocolVersion=CURRENT_PROTOCOL_VERSION
    )
    result = read_manager.get_result(get_request)

    proof = extract_proof(result, multi_sig)
    attr_value = result[DATA]
    assert attr_value == raw_attribute

    # Verifying signed state proof
    path = domain.make_state_path_for_attr(nym, attr_key)
    assert is_proof_verified(db_manager,
                             proof, path,
                             domain.hash_of(attr_value), seq_no, txn_time)


def test_state_proofs_for_get_claim_def(write_manager,
                                        read_manager,
                                        db_manager):
    # Adding claim def
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'

    seq_no = 0
    txn_time = int(time.time())
    identifier = "6ouriXMZkLeHsuXrN1X1fd"

    schema_seqno = 0
    signature_type = 'CL'
    key_components = '{"key_components": []}'
    tag = 'tag1'

    txn = {
        TXN_TYPE: CLAIM_DEF,
        TARGET_NYM: nym,
        CLAIM_DEF_SCHEMA_REF: schema_seqno,
        CLAIM_DEF_PUBLIC_KEYS: key_components,
        CLAIM_DEF_TAG: tag
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=txn,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    txn = append_payload_metadata(txn, frm=nym)

    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)

    # Getting claim def
    request = Request(
        operation={
            IDENTIFIER: nym,
            CLAIM_DEF_FROM: nym,
            CLAIM_DEF_SCHEMA_REF: schema_seqno,
            CLAIM_DEF_SIGNATURE_TYPE: signature_type,
            CLAIM_DEF_TAG: tag,
            TXN_TYPE: GET_CLAIM_DEF,
        },
        signatures={},
        protocolVersion=CURRENT_PROTOCOL_VERSION
    )

    result = read_manager.get_result(request)
    proof = extract_proof(result, multi_sig)
    assert result[DATA] == key_components

    # Verifying signed state proof
    path = domain.make_state_path_for_claim_def(nym, schema_seqno,
                                                signature_type, tag)
    assert is_proof_verified(db_manager,
                             proof, path,
                             key_components, seq_no, txn_time)


def test_state_proofs_for_get_context(write_manager,
                                      read_manager,
                                      db_manager):
    # Adding context
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'

    seq_no = 0
    txn_time = int(time.time())
    identifier = "6ouriXMZkLeHsuXrN1X1fd"

    context_name = "context_a"
    context_version = "1.0"
    meta = {CONTEXT_NAME: context_name,
            CONTEXT_VERSION: context_version,
            RS_TYPE: CONTEXT_TYPE}
    data = {CONTEXT_CONTEXT: {"ex": "https://example.org/examples#"}}
    txn = {
        TXN_TYPE: SET_CONTEXT,
        DATA: data,
        META: meta
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=txn,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    txn = append_payload_metadata(txn, frm=nym)

    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)

    # Getting context
    request = Request(
        operation={
            TARGET_NYM: nym,
            META: meta,
            TXN_TYPE: GET_CONTEXT
        },
        signatures={},
        protocolVersion=CURRENT_PROTOCOL_VERSION
    )

    result = read_manager.get_result(request)
    proof = extract_proof(result, multi_sig)
    assert result[DATA][DATA] == data

    # Verifying signed state proof
    path = domain.make_state_path_for_context(nym, context_name, context_version)
    value = {
        META: meta,
        DATA: data
    }
    assert is_proof_verified(db_manager,
                             proof, path,
                             value, seq_no, txn_time)


def test_state_proofs_for_get_schema(write_manager,
                                     read_manager,
                                     db_manager):
    # Adding schema
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'

    seq_no = 0
    txn_time = int(time.time())
    identifier = "6ouriXMZkLeHsuXrN1X1fd"

    schema_name = "schema_a"
    schema_version = "1.0"
    # data = '{"name": "schema_a", "version": "1.0"}'
    schema_key = {SCHEMA_NAME: schema_name,
                  SCHEMA_VERSION: schema_version}
    data = {**schema_key,
            SCHEMA_ATTR_NAMES: ["Some_Attr", "Attr1"]}
    txn = {
        TXN_TYPE: SCHEMA,
        DATA: data,
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=txn,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    txn = append_payload_metadata(txn, frm=nym)

    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)

    # Getting schema
    request = Request(
        operation={
            TARGET_NYM: nym,
            DATA: schema_key,
            TXN_TYPE: GET_SCHEMA
        },
        signatures={},
        protocolVersion=CURRENT_PROTOCOL_VERSION
    )

    result = read_manager.get_result(request)
    proof = extract_proof(result, multi_sig)
    assert result[DATA] == data

    data.pop(NAME)
    data.pop(VERSION)

    # Verifying signed state proof
    path = domain.make_state_path_for_schema(nym, schema_name, schema_version)
    assert is_proof_verified(db_manager,
                             proof, path,
                             data, seq_no, txn_time)


def prep_multi_sig(write_manager, db_manager, nym, role, verkey, seq_no):
    txn_time = int(time.time())
    identifier = "6ouriXMZkLeHsuXrN1X1fd"

    # Adding nym
    data = {
        TARGET_NYM: nym,
        ROLE: role,
        VERKEY: verkey,
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=data,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    txn = set_type(txn, NYM)
    txn = append_payload_metadata(txn, frm=nym)
    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)
    return data, multi_sig


def get_nym_verify_proof(read_manager,
                         db_manager,
                         nym, data, multi_sig):
    request = Request(
        operation={
            TARGET_NYM: nym,
            TXN_TYPE: GET_NYM,
        },
        signatures={},
        protocolVersion=CURRENT_PROTOCOL_VERSION
    )
    result = read_manager.get_result(request)
    proof = extract_proof(result, multi_sig)
    result_data = None

    assert proof
    if data:
        assert result[DATA]
        result_data = domain_state_serializer.deserialize(result[DATA])
        data_from_req = copy.copy(result_data)
        data_from_req.pop(f.IDENTIFIER.nm, None)
        data_from_req.pop(f.SEQ_NO.nm, None)
        data_from_req.pop(TXN_TIME, None)
        assert data_from_req == data

    # Verifying signed state proof
    path = nym_to_state_key(nym)
    # If the value does not exist, serialization should be null and
    # verify_state_proof needs to be given null (None). This is done to
    # differentiate between absence of value and presence of empty string value
    if result_data:
        data.pop(TARGET_NYM, None)
        data.update({f.IDENTIFIER.nm: result_data[f.IDENTIFIER.nm],
                     f.SEQ_NO.nm: result_data[f.SEQ_NO.nm],
                     TXN_TIME: result_data[TXN_TIME]})
    serialized_value = domain_state_serializer.serialize(data) if data else None
    proof_nodes = base64.b64decode(proof[PROOF_NODES])
    root_hash = base58.b58decode(proof[ROOT_HASH])
    return db_manager.get_state(DOMAIN_LEDGER_ID).verify_state_proof(
        root_hash,
        path,
        serialized_value,
        proof_nodes,
        serialized=True
    )


def test_state_proofs_for_get_nym(write_manager,
                                  read_manager,
                                  db_manager):
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'
    role = "2"
    verkey = "~7TYfekw4GUagBnBVCqPjiC"
    seq_no = 1
    # Check for existing nym
    data, multi_sig = prep_multi_sig(write_manager, db_manager, nym, role, verkey, seq_no)
    assert get_nym_verify_proof(read_manager, db_manager, nym, data, multi_sig)

    # Shuffle the bytes of nym
    h = list(friendlyToHex(nym))
    random.shuffle(h)
    garbled_nym = hexToFriendly(bytes(h))
    data[f.IDENTIFIER.nm] = garbled_nym
    # `garbled_nym` does not exist, proof should verify but data is null
    assert get_nym_verify_proof(read_manager, db_manager, garbled_nym, None, multi_sig)


def test_no_state_proofs_if_protocol_version_less(write_manager,
                                                  read_manager,
                                                  db_manager):
    nym = 'Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv'
    role = "2"
    verkey = "~7TYfekw4GUagBnBVCqPjiC"
    identifier = "6ouriXMZkLeHsuXrN1X1fd"

    seq_no = 0
    txn_time = int(time.time())
    # Adding nym
    data = {
        TARGET_NYM: nym,
        ROLE: role,
        VERKEY: verkey,
    }
    txn = append_txn_metadata(reqToTxn(Request(operation=data,
                                               protocolVersion=CURRENT_PROTOCOL_VERSION,
                                               identifier=identifier)),
                              seq_no=seq_no, txn_time=txn_time)
    txn = set_type(txn, NYM)
    txn = append_payload_metadata(txn, frm=nym)
    write_manager.update_state(txn)
    db_manager.get_state(DOMAIN_LEDGER_ID).commit()
    multi_sig = save_multi_sig(db_manager)

    # Getting nym
    request = Request(
        operation={
            TARGET_NYM: nym,
            TXN_TYPE: GET_NYM,
        },
        signatures={}
    )
    result = read_manager.get_result(request)
    assert STATE_PROOF not in result
