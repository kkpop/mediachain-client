import json
import sys
from os import walk
from os.path import join
from mediachain.data_objects import Artefact, Entity, ArtefactCreationCell, \
    MultihashReference
from mediachain.datastore.dynamo import DynamoDatastore
from mediachain.transactor import TransactorClient
from datetime import datetime
from grpc.framework.interfaces.face.face import AbortionError

TRANSLATOR_ID = u'GettyTranslator/0.1'


def ingest(host, port, dir_root, datastore_url=None, max_num=0):
    transactor = TransactorClient(host, port)
    datastore = DynamoDatastore(endpoint_url=datastore_url)

    try:
        for artefact, artefact_ref, entity_ref, cell_ref in getty_artefacts(
            transactor, dir_root, max_num, datastore
        ):
            getty_id = artefact.meta[u'data'][u'_id']
            print "Ingested getty image '{0}' {1}".format(
                getty_id, artefact_ref
            )
    except AbortionError as e:
        print("RPC Error: " + str(e))


def getty_to_mediachain_objects(transactor, raw_ref, getty_json, entities):
    common_meta = {u'rawRef': raw_ref.to_map(),
                   u'translatedAt': unicode(datetime.utcnow().isoformat()),
                   u'translator': TRANSLATOR_ID}

    artist_name = getty_json['artist']
    if artist_name in entities:
        entity_ref = entities[artist_name]
    else:
        artist_meta = dict(common_meta, data={u'name': artist_name})
        entity = Entity(artist_meta)
        entity_ref = transactor.insert(entity)

    data = {u'_id': u'getty_' + getty_json['id'],
            u'title': getty_json['title'],
            u'artist': getty_json['artist'],
            u'collection_name': getty_json['collection_name'],
            u'caption': getty_json['caption'],
            u'editorial_source': getty_json['editorial_source'].get('name', None),
            u'keywords':
                [x['text'] for x in getty_json['keywords'] if 'text' in x],
            u'date_created': getty_json['date_created']
            }

    artefact_meta = dict(common_meta, data=data)
    artefact = Artefact(artefact_meta)

    artefact_ref = transactor.insert(artefact)
    creation_cell = ArtefactCreationCell(meta=common_meta,
                                         ref=artefact_ref,
                                         entity=entity_ref)

    cell_ref = transactor.update(creation_cell)
    return artefact, artefact_ref, entity_ref, cell_ref


def getty_artefacts(transactor,
                    dd='getty/json/images',
                    max_num=0,
                    datastore=DynamoDatastore()):
    entities = dedup_artists(transactor, dd, max_num, datastore)

    for content, getty_json in walk_json_dir(dd, max_num):
        raw_ref_str = datastore.put(content)
        raw_ref = MultihashReference.from_base58(raw_ref_str)
        yield getty_to_mediachain_objects(
            transactor, raw_ref, getty_json, entities
        )


def dedup_artists(transactor,
                  dd='getty/json/images',
                  max_num=0,
                  datastore=DynamoDatastore()):
    print("deduplicating getty artists.  This may take a while...")
    artist_name_map = {}
    total_parsed = 0
    unique = 0
    for content, getty_json in walk_json_dir(dd, max_num):
        total_parsed += 1
        n = getty_json['artist']
        if n is None or n in artist_name_map:
            continue
        unique += 1
        raw_ref_str = datastore.put(content)
        artist_name_map[n] = raw_ref_str

        sys.stdout.write('\r')
        sys.stdout.write('Parsed {0} entries, {1} unique artists'.format(
            total_parsed, unique
        ))
        sys.stdout.flush()

    entities = {}
    for n, raw_ref_str in artist_name_map.iteritems():
        meta = {u'rawRef': MultihashReference.from_base58(raw_ref_str).to_map(),
                u'translatedAt': unicode(datetime.utcnow().isoformat()),
                u'translator': TRANSLATOR_ID,
                u'data': {u'name': n}}
        entity = Entity(meta)
        try:
            ref = transactor.insert(entity)
            entities[n] = ref
        except AbortionError as e:
            print("RPC error inserting entity: " + str(e))

    print('\ndone deduplicating artists')
    return entities


def walk_json_dir(dd='getty/json/images',
                  max_num=0):
    nn = 0
    for dir_name, subdir_list, file_list in walk(dd):
        for fn in file_list:
            nn += 1

            if max_num and (nn + 1 >= max_num):
                print ('ENDING EARLY')
                return

            fn = join(dir_name, fn)

            with open(fn, mode='rb') as f:
                try:
                    content = f.read()
                    decoded_json = json.loads(content.decode('utf-8'))
                    yield content, decoded_json
                except ValueError:
                    print "couldn't decode json from {}".format(fn)
                    continue
