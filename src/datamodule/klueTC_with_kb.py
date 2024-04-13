import os
import re
import json
import numpy as np
import pandas as pd
from omegaconf import DictConfig

import logging
logging.basicConfig(level=logging.INFO)

import torch
from torch.utils.data import DataLoader
from torch.utils.data import Dataset

from transformers import BertTokenizerFast, ElectraTokenizer, PreTrainedTokenizerFast, T5Tokenizer

class DataModule(Dataset):
    def __init__(self, 
            tokenizer=None, 
            data_path=None, 
            **kwargs
        ):

        self.config = DictConfig(kwargs)
        self.tokenizer = tokenizer

        self.data = self.load_data(data_path)
        logging.info(f"LOAD {data_path}")
        self.labels = self.set_labels() 

        max_length = 0
        if self.config.check_length:
            max_length = self.check_length([d['inputs'] for d in self.data])
        if not self.config.max_source_length:
            self.config.max_source_length = max_length

        assert self.config.max_source_length > 0, \
                f'Need datamodule.config.max_source_length > 0 or datamodule.config.check_length is True. '+\
                f'But datamodule.config.max_source_length is {self.config.max_source_length} and datamodule.config.check_length is {self.config.check_length}'


    def __len__(self):
        return len(self.data)

    ############ TODO: custom zone #####################
    
    ## Must check inputs_name & labels_name
    inputs_name = 'source'
    labels_name = 'target'

    def load_data(self, filename): 
        
        with open(filename, 'r') as f:
            data = [json.loads(d) for d in f]
        for item in data:
            item['inputs'] = item.pop(self.inputs_name)
            item['labels'] = item.pop(self.labels_name)
        return data
    
    def convert_dict_to_kb(self, data):
        result = list()
        for item in data:
            inputs = item[self.inputs_name]
            labels = item[self.labels_name]
            string = f'<x> {inputs.strip()} </x> is <y> {labels.strip()} </y>.' ## TODO
            string = bytes(string, 'utf-8')
            result.append(string)
        return np.array(result, dtype=object)
    
    def __getitem__(self, index):
        data = self.data[index].copy()
        source = data['inputs'].strip()
        source = f'<x> {source} </x> is <y>'
        inputs = self.convert_sentence_to_input(source, self.config.max_source_length)

        target = data['labels'].strip()
        #target = f'{source} {target} </y> 이다.'
        #labels = self.convert_sentence_to_input(target, self.config.max_target_length)
        labels = target

        return {'inputs':inputs, 'labels':labels, 'data':data}


    #########################################

    def get_kb(self, data_path=None):
        ext = os.path.splitext(data_path)[-1]
        
        if os.path.exists(data_path.replace(ext,'npy')):
            data = np.load(data_path, allow_pickle=True)
            return data
            
        if ext in ['.npy']:
            data = np.load(data_path, allow_pickle=True)
        elif ext in ['.txt']:
            with open(data_path, 'r') as f:
                data = [bytes(d.strip(), 'utf-8') for d in f]
            data = np.array(data, dtype=object)
        elif ext in ['.jsonl']:
            with open(data_path, 'r') as f:
                data = [json.loads(d) for d in f]
            data = self.convert_dict_to_kb(data)

        if ext not in ['.npy']:
            save_path = data_path.replace(ext,'.npy')
            np.save(save_path, data)
            logging.info(f'SAVE KB data: {save_path}')
        return data

    def set_labels(self):
        if self.config.labels:
            labels_path = os.path.join(self.config.data_dir, self.config.labels)
            with open(labels_path,'r') as f:
                return [d.strip() for d in f]
        else:
            targets = [d['labels'] for d in self.data]
            return sorted(set(targets))

    def get_labels(self):
        return self.labels

#     def set_tokenizer(self, tokenizer):
#         self.tokenizer = tokenizer
#     def get_tokenizer(self):
#         return self.tokenizer

    def check_length(self, data):
        length = list()
        for item in data:
            tokenized_source = self.tokenizer.tokenize(item)
            length.append(len(tokenized_source))
        logging.info(f'CHECK Length:\n{pd.Series(length).describe()}')
        max_length = max(length+[0]) 
        return max_length
        
    def get_dataset(self):
        ## equal self class
        return self 

    def get_dataloader(self, sampler=None):
        dataloader = DataLoader(self,
                batch_size = self.config.batch_size, 
                shuffle = self.config.shuffle,
                num_workers = self.config.num_workers,
                sampler = sampler,
                collate_fn = lambda data: self.collate_fn(data))
        return dataloader

    def clean_text(self, text):
        text = text.strip()
        #text = re.sub('\[[^\]]*\]','',text) ## 대괄호 제거
        #text = re.sub('\([^\)]*\)','',text) ## 소괄호 제거
        #text = re.sub('[^ㅏ-ㅣㄱ-ㅎ가-힣0-9a-zA-Z\.%, ]',' ', text) ## 특수문자 모두 제거
        text = re.sub('  *',' ',text).strip() ## 다중 공백 제거
        return text

    def convert_sentence_to_input(self, inputs, max_len, direction='right', special_token=False):
        ## tokenizer.encode(text, max_length=max_length, padding='max_length', truncation=True)
        inputs = self.tokenizer.tokenize(inputs)
        return self.convert_tokens_to_input(inputs, max_len, direction=direction, special_token=special_token)
    
    def convert_tokens_to_input(self, inputs, max_len, direction='right', special_token=False):
        if special_token:
            inputs = [self.tokenizer.cls_token] + inputs + [self.tokenizer.sep_token] ## for bert

        dif = abs(max_len - len(inputs))
        if direction == 'left':
            if len(inputs) < max_len:  inputs = ( [self.tokenizer.pad_token] * dif ) + inputs
            elif max_len < len(inputs):  inputs = inputs[dif:]
        else:
            if len(inputs) < max_len:  inputs += [self.tokenizer.pad_token] * dif
            elif max_len < len(inputs):  inputs = inputs[:max_len]

        inputs = self.tokenizer.convert_tokens_to_ids(inputs)
        return inputs

    def convert_input_to_tokens(self, inputs, special_token=False):
        return self.tokenizer.convert_ids_to_tokens(inputs, skip_special_tokens=special_token)

    def convert_input_to_sentence(self, inputs, special_token=False):
        return self.tokenizer.decode(inputs, skip_special_tokens=special_token)

    def collate_fn(self, data):
        result = {
                'input_ids': [d['inputs'] for d in data],
                'labels': [d['labels'] for d in data],
                'data': [d['data'] for d in data]
                }

        for key in [d for d in result if d not in ['data', 'labels']]:
            result[key] = torch.tensor(result[key])

        return result 


