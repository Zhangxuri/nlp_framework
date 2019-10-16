# Encoder-Decoder Framework for NLP
This code can help NLPer to pay more attention to **model** rather than data and log and ...

***********************************************************

## Requirements
* Ubuntu 16.0.4+
* Python 3.5+
* Pytorch 0.4.1+ (updated)


**************************************************************

## Preprocessing
```
python3 preprocess.py -load_data path_to_data -save_data path_to_store_data 
```
Remember to put the data into a folder and name them *train.src*, *train.tgt*, *valid.src*, *valid.tgt*, and make a new folder inside called *data*

***************************************************************

## Training
```
python3 train.py -log log_name -config config_yaml -gpus id
```
Create your own yaml file for hyperparameter setting.

****************************************************************


<!-- ## Evaluation
```
python3 train.py -log log_name -config config_yaml -gpus id -restore checkpoint -mode eval
```

******************************************************************* -->

# Citation
If you use this code for your research, please kindly cite our paper:
```
@inproceedings{globalencoding,
  title     = {Global Encoding for Abstractive Summarization},
  author    = {Junyang Lin and Xu Sun and Shuming Ma and Qi Su},
  booktitle = {{ACL} 2018},
  year      = {2018}
}
```
