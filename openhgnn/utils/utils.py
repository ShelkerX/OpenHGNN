import dgl
import copy
from dgl import backend as F
import torch as th
from scipy.sparse import coo_matrix
import numpy as np
import random
from . import load_HIN, load_KG, load_OGB, BEST_CONFIGS
import datetime


def set_best_config(args):
    configs = BEST_CONFIGS[args.task]
    if args.model not in configs:
        return args
    configs = configs[args.model]
    for key, value in configs["general"].items():
        args.__setattr__(key, value)
    if args.dataset not in configs:
        return args
    for key, value in configs[args.dataset].items():
        args.__setattr__(key, value)
    return args


class EarlyStopping(object):
    def __init__(self, patience=10):
        self.patience = patience
        self.counter = 0
        self.best_score = None
        self.best_loss = None
        self.early_stop = False
        self.best_model = None

    def step(self, loss, score, model):
        if self.best_loss is None:
            self.best_score = score
            self.best_loss = loss
            self.best_model = copy.deepcopy(model)
        elif (loss > self.best_loss) and (score < self.best_score):
            self.counter += 1
            #print(f'EarlyStopping counter: {self.counter} out of {self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            if (loss <= self.best_loss) and (score >= self.best_score):
                self.best_model = copy.deepcopy(model)
            self.best_loss = np.min((loss, self.best_loss))
            self.best_score = np.max((score, self.best_score))
            self.counter = 0
        return self.early_stop


def get_nodes_dict(hg):
    n_dict = {}
    for n in hg.ntypes:
        n_dict[n] = hg.num_nodes(n)
    return n_dict

def extract_embed(node_embed, input_nodes):
    emb = {}

    for ntype, nid in input_nodes.items():
        nid = input_nodes[ntype]
        emb[ntype] = node_embed[ntype][nid]
    return emb


def build_dataset(model_name, dataset_name):
    # load the graph(HIN or KG)
    if dataset_name in ['mag']:
        dataset = load_OGB(dataset_name)
        return dataset
    if model_name in ['GTN', 'NSHE', 'HetGNN']:
        g, category, num_classes = load_HIN(dataset_name)
    elif model_name in ['RSHN', 'RGCN', 'CompGCN']:
        g, category, num_classes = load_KG(dataset_name)
    return g, category, num_classes


def set_random_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    th.manual_seed(seed)
    th.cuda.manual_seed(seed)

def com_mult(a, b):
    r1, i1 = a[..., 0], a[..., 1]
    r2, i2 = b[..., 0], b[..., 1]
    return th.stack([r1 * r2 - i1 * i2, r1 * i2 + i1 * r2], dim=-1)


def conj(a):
    a[..., 1] = -a[..., 1]
    return a


def ccorr(a, b):
    """
    Compute circular correlation of two tensors.
    Parameters
    ----------
    a: Tensor, 1D or 2D
    b: Tensor, 1D or 2D
    Notes
    -----
    Input a and b should have the same dimensions. And this operation supports broadcasting.
    Returns
    -------
    Tensor, having the same dimension as the input a.
    """
    import torch.fft as fft
    return th.irfft(com_mult(conj(th.rfft(a, 1)), th.rfft(b, 1)), 1, signal_sizes=(a.shape[-1],))


def extract_edge_with_id_edge(hg):
    # input a heterogensous graph
    # return graph list
    g = dgl.to_homogeneous(hg, ndata='h')
    edges = g.edges()
    etype = g.edata[dgl.ETYPE]
    h = g.ndata['h']
    ctx = g.device
    #g.edata['w'] = th.ones(g.num_edges(), device=ctx)
    num_edge_type = th.max(etype).item()
    graph_list = []
    for i in range(num_edge_type + 1):
        e_ids = th.nonzero(etype == i).squeeze()
        sg = dgl.graph((edges[0][e_ids], edges[1][e_ids]), num_nodes=g.num_nodes())
        sg.edata['w'] = th.ones(sg.num_edges(), device=ctx)
        graph_list.append(sg)
    x = th.arange(0, g.num_nodes(), device=ctx)
    sg = dgl.graph((x, x))
    sg.edata['w'] = th.ones(g.num_nodes(), device=ctx)
    graph_list.append(sg)

    return graph_list, h


def extract_mtx_with_id_edge(g):
    # input a homogeneous graph
    # return tensor with shape of [2,num_edges]
    edges = g.edges()
    edata = g.edata['_TYPE']
    num_edge_type = th.max(edata).item()
    ctx = F.context(edges[0])
    dtype = F.dtype(edges[0])
    A = []
    num_nodes = g.num_nodes()
    for i in range(num_edge_type + 1):
        index = th.nonzero(edata == i).squeeze()
        e_0 = edges[0][index].to('cpu').numpy()
        e_1 = edges[1][index].to('cpu').numpy()
        values = np.ones(e_0.shape[0])
        m = coo_matrix((values, (e_0, e_1)), shape=(num_nodes, num_nodes))
        m = th.from_numpy(m.todense()).type(th.FloatTensor).unsqueeze(0)
        if 0 == i:
            A = m
        else:
            A = th.cat([A, m], dim=0)
    m = th.eye(num_nodes).unsqueeze(0)
    A = th.cat([A, m], dim=0)
    return A.to(ctx)


def h2dict(h, hdict):
    pre = 0
    for i, value in hdict.items():
        hdict[i] = h[pre:value.shape[0] + pre]
        pre += value.shape[0]
    return hdict


def print_dict(d, end_string='\n\n'):
    for key in d.keys():
        if isinstance(d[key], dict):
            print('\n', end='')
            print_dict(d[key], end_string='')
        elif isinstance(d[key], int):
            print('{}: {:04d}'.format(key, d[key]), end=', ')
        elif isinstance(d[key], float):
            print('{}: {:.4f}'.format(key, d[key]), end=', ')
        else:
            print('{}: {}'.format(key, d[key]), end=', ')
    print(end_string, end='')