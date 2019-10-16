import torch
import torch.utils.data
import lr_scheduler as L
import yaml

import os
import argparse
import pickle
import time
from collections import OrderedDict

import opts
import models
import utils
import codecs

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


parser = argparse.ArgumentParser(description='train.py')
opts.model_opts(parser)

opt = parser.parse_args()
config = utils.read_config(opt.config)
torch.manual_seed(opt.seed)
opts.convert_to_config(opt, config)

# cuda
use_cuda = torch.cuda.is_available() and len(opt.gpus) > 0
config.use_cuda = use_cuda
if use_cuda:
    torch.cuda.set_device(opt.gpus[0])
    torch.cuda.manual_seed(opt.seed)
    torch.backends.cudnn.benchmark = True


def load_data(mode='train'):
    print('loading data...\n')
    data = pickle.load(open(config.data+'data.pkl', 'rb'))
    data['train']['length'] = int(data['train']['length'] * opt.scale)

    trainset = utils.BiDataset(data['train'], char=config.char)
    validset = utils.BiDataset(data['valid'], char=config.char)
    testset = utils.BiDataset(data['test'], char=config.char)

    src_vocab = data['dict']['src']
    tgt_vocab = data['dict']['tgt']
    config.src_vocab_size = src_vocab.size()
    config.tgt_vocab_size = tgt_vocab.size()

    trainloader = torch.utils.data.DataLoader(dataset=trainset,
                                              batch_size=config.batch_size,
                                              shuffle=True,
                                              num_workers=0,
                                              collate_fn=utils.padding)
    if hasattr(config, 'valid_batch_size'):
        valid_batch_size = config.valid_batch_size
    else:
        valid_batch_size = config.batch_size
    
    if hasattr(config, 'test_batch_size'):
        test_batch_size = config.test_batch_size
    else:
        test_batch_size = config.batch_size

    validloader = torch.utils.data.DataLoader(dataset=validset,
                                              batch_size=valid_batch_size,
                                              shuffle=False,
                                              num_workers=0,
                                              collate_fn=utils.padding)
    testloader = torch.utils.data.DataLoader(dataset=testset,
                                              batch_size=test_batch_size,
                                              shuffle=False,
                                              num_workers=0,
                                              collate_fn=utils.padding)

    return {'trainset': trainset, 'validset': validset, 'testset': testset,
            'trainloader': trainloader, 'validloader': validloader, 'testloader': testloader,
            'src_vocab': src_vocab, 'tgt_vocab': tgt_vocab}



def build_model(checkpoints, print_log):
    with open(config.logF+config.log+'/'+'default.yaml', "w") as f:
        yaml.dump(dict(config),f)
    # model
    print('building model...\n')
    model = getattr(models, config.model)(config)
    if checkpoints is not None:
        model.load_state_dict(checkpoints['model'])
    if opt.pretrain:
        print('loading checkpoint from %s' % opt.pretrain)
        pre_ckpt = torch.load(opt.pretrain)['model']
        pre_ckpt = OrderedDict({key[8:]: pre_ckpt[key] for key in pre_ckpt if key.startswith('encoder')})
        print(model.encoder.state_dict().keys())
        print(pre_ckpt.keys())
        model.encoder.load_state_dict(pre_ckpt)
    if use_cuda:
        model.cuda()
    
    # optimizer
    if checkpoints is not None:
        optim = checkpoints['optim']
    else:
        optim = models.Optim(config.optim, config.learning_rate, config.max_grad_norm,
                             lr_decay=config.learning_rate_decay, start_decay_at=config.start_decay_at)
    optim.set_parameters(model.parameters())

    # print log
    param_count = 0
    for param in model.parameters():
        param_count += param.view(-1).size()[0]

    print_log("\n")
    print_log(repr(model) + "\n\n")
    print_log('total number of parameters: %d\n\n' % param_count)

    return model, optim, print_log


def train_model(model, data, optim, epoch, params):

    model.train()
    trainloader = data['trainloader']

    for src, tgt, src_len, tgt_len, original_src, original_tgt in trainloader:

        model.zero_grad()

        if config.use_cuda:
            src = src.cuda()
            tgt = tgt.cuda()
            src_len = src_len.cuda()
        lengths, indices = torch.sort(src_len, dim=0, descending=True)
        src = torch.index_select(src, dim=0, index=indices)
        tgt = torch.index_select(tgt, dim=0, index=indices)
        targets = tgt[:, 1:]

# src.size=[batch_size,max_src_len_in_this_batch], reversed id
# tgt.size=[batch_size,max_tgt_len_in_this_batch], non-reversed id
#  example:
#   dict =  <blank> 0
#           <unk>  1
#           <s> 2
#           </s> 3
#           hello 4
#           word 5
#  original_src=[['hello','world'],['hello','hello','world']]
#  original_tgt=[['hello','world'],['hello','hello','world']]
#  src=[[5,4,4],[5,4,0]]
#  tgt=[[2,4,5,3,0],[2,4,4,5,3]]

        try:
            if config.schesamp:
                if epoch > 8:
                    e = epoch - 8
                    loss, outputs = model(src, tgt,  teacher_ratio=0.9**e)
                else:
                    loss, outputs = model(src, tgt)
            else:
                loss, outputs = model(src,tgt)
            pred = outputs.max(2)[1]
            targets = targets.t()
            num_correct = pred.eq(targets).masked_select(targets.ne(utils.PAD)).sum().item()
            num_total = targets.ne(utils.PAD).sum().item()
            if config.max_split == 0:
                loss = torch.sum(loss) / num_total
                loss.backward()
            optim.step()

            params['report_loss'] += loss.item()
            params['report_correct'] += num_correct
            params['report_total'] += num_total

        except RuntimeError as e:
            if 'out of memory' in str(e):
                print('| WARNING: ran out of memory')
                if hasattr(torch.cuda, 'empty_cache'):
                    torch.cuda.empty_cache()
            else:
                raise e

        utils.progress_bar(params['updates'], config.eval_interval)
        params['updates'] += 1

        if params['updates'] % config.eval_interval == 0:
            params['log']("epoch: %3d, loss: %6.3f, time: %6.3f, updates: %8d, accuracy: %2.2f\n"
                          % (epoch, params['report_loss'], time.time()-params['report_time'],
                             params['updates'], params['report_correct'] * 100.0 / params['report_total']))
            print('evaluating after %d updates...\r' % params['updates'])
            score = eval_model(model, data, params)
            for metric in config.metrics:
                params[metric].append(score[metric])
                if score[metric] >= max(params[metric]):
                    with codecs.open(params['log_path']+'best_'+metric+'_prediction.txt','w','utf-8') as f:
                        f.write(codecs.open(params['log_path']+'candidate.txt','r','utf-8').read())
                    save_model(params['log_path']+'best_'+metric+'_checkpoint.pt', model, optim, params['updates'])
            model.train()
            params['report_loss'], params['report_time'] = 0, time.time()
            params['report_correct'], params['report_total'] = 0, 0

        if params['updates'] % config.save_interval == 0:
            save_model(params['log_path']+'checkpoint.pt', model, optim, params['updates'])

    optim.updateLearningRate(score=0, epoch=epoch)


def eval_model(model, data, params, mode='eval'):

    model.eval()
    reference, candidate, source, alignments = [], [], [], []
    if mode == 'test':
        count, total_count = 0, len(data['validset'])
        validloader = data['validloader']
    else:
        count, total_count = 0, len(data['validset'])
        validloader = data['validloader']       
    tgt_vocab = data['tgt_vocab']


    for src, tgt, src_len, tgt_len, original_src, original_tgt in validloader:

        if config.use_cuda:
            src = src.cuda()
            src_len = src_len.cuda()

        with torch.no_grad():
            if config.beam_size > 1:
                samples, alignment, weight = model.beam_sample(src, beam_size=config.beam_size, eval_=True)
            else:
                samples, alignment = model.sample(src)

        candidate += [tgt_vocab.convertToLabels(s, utils.EOS) for s in samples]
        source += original_src
        reference += original_tgt
        if alignment is not None:
            alignments += [align for align in alignment]

        count += len(original_src)
        utils.progress_bar(count, total_count)

    if config.unk and config.attention != 'None':
        cands = []
        for s, c, align in zip(source, candidate, alignments):
            cand = []
            for word, idx in zip(c, align):
                if word == utils.UNK_WORD and idx < len(s):
                    try:
                        cand.append(s[idx])
                    except:
                        cand.append(word)
                        print("%d %d\n" % (len(s), idx))
                else:
                    cand.append(word)
            cands.append(cand)
            if len(cand) == 0:
                print('Error!')
        candidate = cands

    with codecs.open(params['log_path']+'candidate.txt','w+','utf-8') as f:
        for i in range(len(candidate)):
            f.write(" ".join(candidate[i])+'\n')

    score = {}
    for metric in config.metrics:
        score[metric] = getattr(utils, metric)(reference, candidate, params['log_path'], params['log'], config)

    return score


def save_model(path, model, optim, updates):
    model_state_dict = model.state_dict()
    checkpoints = {
        'model': model_state_dict,
        'config': config,
        'optim': optim,
        'updates': updates}
    torch.save(checkpoints, path)


def build_log():
    # log
    if not os.path.exists(config.logF):
        os.mkdir(config.logF)
    if opt.log == '':
        log_path = config.logF + str(int(time.time() * 1000)) + '/'
    else:
        log_path = config.logF + opt.log + '/'
    if not os.path.exists(log_path):
        os.mkdir(log_path)
    print_log = utils.print_log(log_path + 'log.txt')
    return print_log, log_path


def showAttention(path, s, c, attentions, index):
    # Set up figure with colorbar
    fig = plt.figure()
    ax = fig.add_subplot(111)
    cax = ax.matshow(attentions.numpy(), cmap='bone')
    fig.colorbar(cax)
    # Set up axes
    ax.set_xticklabels([''] + s, rotation=90)
    ax.set_yticklabels([''] + c)
    # Show label at every tick
    ax.xaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
    plt.show()
    plt.savefig(path + str(index) + '.jpg')


def main():
    # checkpoint
    if opt.restore:
        print('loading checkpoint...\n')
        checkpoints = torch.load(opt.restore, map_location = 'cuda:%d' % opt.gpus[0])
    else:
        checkpoints = None

    data = load_data(opt.mode)
    print_log, log_path = build_log()
    model, optim, print_log = build_model(checkpoints, print_log)
    # scheduler
    if config.schedule:
        scheduler = L.CosineAnnealingLR(optim.optimizer, T_max=config.epoch)
    params = {'updates': 0, 'report_loss': 0, 'report_total': 0,
              'report_correct': 0, 'report_time': time.time(),
              'log': print_log, 'log_path': log_path}
    for metric in config.metrics:
        params[metric] = []
    if opt.restore:
        params['updates'] = checkpoints['updates']

    if opt.mode == "train":
        for i in range(1, config.epoch + 1):
            if config.schedule:
                scheduler.step()
                print("Decaying learning rate to %g" % scheduler.get_lr()[0])
            train_model(model, data, optim, i, params)
        for metric in config.metrics:
            print_log("Best %s score: %.2f\n" % (metric, max(params[metric])))
    else:
        score = eval_model(model, data, params, opt.mode)


if __name__ == '__main__':
    main()
