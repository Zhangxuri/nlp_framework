# Introduction
## directory structure
```bash
.
├── raw_data # download your data here.
│   └── dd # dataset name.
├── README.md 
├── src_data # prepreprocessed data here, it contains train.src,train.tgt,valid.src,valid.tgt
│   └── dd
└── trg_data # use preprocess.py to process data here. model will get data here
    └── dd
```