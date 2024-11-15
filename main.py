from tqdm import tqdm
import network
import utils
import os
import random
import argparse
import numpy as np

from torch.utils import data
from torchvision.utils import save_image
from torchvision.transforms.functional import to_pil_image

from datasets import VOCSegmentation, Cityscapes
from utils import ext_transforms as et
from metrics import StreamSegMetrics

import torch
import torch.nn as nn
from utils.visualizer import Visualizer

from PIL import Image
import matplotlib
import matplotlib.pyplot as plt

import torch
torch.cuda.empty_cache()



def get_argparser():
    parser = argparse.ArgumentParser()

    # Datset Options
    parser.add_argument("--data_root", type=str, default='./datasets/data',
                        help="path to Dataset")
    parser.add_argument("--dataset", type=str, default='voc',
                        choices=['voc', 'cityscapes'], help='Name of dataset')
    parser.add_argument("--num_classes", type=int, default=None,
                        help="num classes (default: None)")

    # Deeplab Options
    parser.add_argument("--model", type=str, default='deeplabv3plus_mobilenet',
                        choices=['deeplabv3_resnet50',  'deeplabv3plus_resnet50',
                                 'deeplabv3_resnet101', 'deeplabv3plus_resnet101',
                                 'deeplabv3_mobilenet', 'deeplabv3plus_mobilenet'], help='model name')
    parser.add_argument("--separable_conv", action='store_true', default=False,
                        help="apply separable conv to decoder and aspp")
    parser.add_argument("--output_stride", type=int, default=16, choices=[8, 16])

    # Train Options
    parser.add_argument("--test_only", action='store_true', default=False)
    parser.add_argument("--save_val_results", action='store_true', default=False,
                        help="save segmentation results to \"./results\"")
    parser.add_argument("--save_val_results_path",type = str,  default='./results/',
                        help="save segmentation results to \"./results\"")
    parser.add_argument("--total_itrs", type=int, default=30e3,
                        help="epoch number (default: 30k)")
    parser.add_argument("--lr", type=float, default=0.01,
                        help="learning rate (default: 0.01)")
    parser.add_argument("--lr_policy", type=str, default='poly', choices=['poly', 'step'],
                        help="learning rate scheduler policy")
    parser.add_argument("--step_size", type=int, default=10000)
    parser.add_argument("--crop_val", action='store_true', default=False,
                        help='crop validation (default: False)')
    parser.add_argument("--batch_size", type=int, default=4,
                        help='batch size (default: 16)')
    parser.add_argument("--val_batch_size", type=int, default=4,
                        help='batch size for validation (default: 4)')
    parser.add_argument("--crop_size", type=int, default=4)

    parser.add_argument("--ckpt", default=None, type=str,
                        help="restore from checkpoint")
    parser.add_argument("--continue_training", action='store_true', default=False)

    parser.add_argument("--loss_type", type=str, default='cross_entropy',
                        choices=['cross_entropy', 'focal_loss'], help="loss type (default: False)")
    parser.add_argument("--gpu_id", type=str, default='0',
                        help="GPU ID")
    parser.add_argument("--weight_decay", type=float, default=1e-4,
                        help='weight decay (default: 1e-4)')
    parser.add_argument("--random_seed", type=int, default=1,
                        help="random seed (default: 1)")
    parser.add_argument("--print_interval", type=int, default=10,
                        help="print interval of loss (default: 10)")
    parser.add_argument("--val_interval", type=int, default=100,
                        help="epoch interval for eval (default: 100)")
    parser.add_argument("--download", action='store_true', default=False,
                        help="download datasets")

    # PASCAL VOC Options
    parser.add_argument("--year", type=str, default='2012',
                        choices=['2012_aug', '2012', '2011', '2009', '2008', '2007'], help='year of VOC')

    # Visdom options
    parser.add_argument("--enable_vis", action='store_true', default=False,
                        help="use visdom for visualization")
    parser.add_argument("--vis_port", type=str, default='13570',
                        help='port for visdom')
    parser.add_argument("--vis_env", type=str, default='main',
                        help='env for visdom')
    parser.add_argument("--vis_num_samples", type=int, default=8,
                        help='number of samples for visualization (default: 8)')
    return parser


def get_dataset(opts):
    """ Dataset And Augmentation
    """
    test_dst = []
    if opts.dataset == 'voc':
        train_transform = et.ExtCompose([
            #et.ExtResize(size=opts.crop_size),
            et.ExtRandomScale((0.5, 2.0)),
            et.ExtRandomCrop(size=(opts.crop_size, opts.crop_size), pad_if_needed=True),
            et.ExtRandomHorizontalFlip(),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])
        if opts.crop_val:
            val_transform = et.ExtCompose([
                et.ExtResize(opts.crop_size),
                et.ExtCenterCrop(opts.crop_size),
                et.ExtToTensor(),
                et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
            ])
        else:
            val_transform = et.ExtCompose([
                et.ExtToTensor(),
                et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
            ])
        train_dst = VOCSegmentation(root=opts.data_root, year=opts.year,
                                    image_set='train', download=opts.download, transform=train_transform)
        val_dst = VOCSegmentation(root=opts.data_root, year=opts.year,
                                  image_set='val', download=False, transform=val_transform)
        if opts.test_only:
            path = os.path.join(opts.data_root, '../../test/')
            paths = sorted([os.path.join(path, x) for x in os.listdir(path)])
            imgs = [Image.open(x).convert('RGB') for x in paths]
            imgs = [val_transform(img,img) for img in imgs]
            test_dst = imgs

    if opts.dataset == 'cityscapes':
        train_transform = et.ExtCompose([
            #et.ExtResize( 512 ),
            et.ExtRandomCrop(size=(opts.crop_size, opts.crop_size)),
            et.ExtColorJitter( brightness=0.5, contrast=0.5, saturation=0.5 ),
            et.ExtRandomHorizontalFlip(),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])

        val_transform = et.ExtCompose([
            #et.ExtResize( 512 ),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])


        train_dst = Cityscapes(root=opts.data_root,
                               split='train', transform=train_transform)

        val_dst = Cityscapes(root=opts.data_root,
                             split='val', transform=val_transform)
        test_dst = Cityscapes(root=opts.data_root,
                               split='test', transform=train_transform)
        
#        if opts.test_only:
#            path = os.path.join(opts.data_root, 'leftImg8bit/tests/')
#            paths = sorted([os.path.join(path, x) for x in os.listdir(path)])
#            imgs = [Image.open(x).convert('RGB') for x in paths]
#            imgs = [val_transform(img,img) for img in imgs]
#            test_dst = imgs
#        else:
#            test_dst = val_dst

    return train_dst, val_dst, test_dst



def validate(opts, model, loader, device, metrics, ret_samples_ids=None):
    print("validating")
    """Do validation and return specified samples"""
    metrics.reset()
    ret_samples = []
    if opts.save_val_results:
        if not os.path.exists(opts.save_val_results_path):
            os.mkdir(opts.save_val_results_path)
        denorm = utils.Denormalize(mean=[0.485, 0.456, 0.406], 
                                   std=[0.229, 0.224, 0.225])
        img_id = 0

    
    with torch.no_grad():
        for i, (images, labels) in tqdm(enumerate(loader)):
            
            images = images.to(device, dtype=torch.float32)
            labels = labels.to(device, dtype=torch.long)

            outputs = model(images)
            preds = outputs.detach().max(dim=1)[1].cpu().numpy()
            targets = labels.cpu().numpy()

            metrics.update(targets, preds)
            if ret_samples_ids is not None and i in ret_samples_ids:  # get vis samples
                ret_samples.append(
                    (images[0].detach().cpu().numpy(), targets[0], preds[0]))

            if opts.save_val_results:
                for i in range(len(images)):
                    image = images[i].detach().cpu().numpy()
                    target = targets[i]
                    pred = preds[i]

                    image = (denorm(image) * 255).transpose(1, 2, 0).astype(np.uint8)
                    target = loader.dataset.decode_target(target).astype(np.uint8)
                    pred = loader.dataset.decode_target(pred).astype(np.uint8)

#                    Image.fromarray(image).save(os.path.join(opts.save_val_results_path,'%d_image.png' % img_id))
#                    Image.fromarray(target).save(os.path.join(opts.save_val_results_path,'%d_target.png' % img_id))
#                    Image.fromarray(pred).save(os.path.join(opts.save_val_results_path,'%d_pred.png' % img_id))

                    fig = plt.figure()
                    plt.imshow(image)
                    plt.axis('off')
                    plt.imshow(pred, alpha=0.7)
                    ax = plt.gca()
                    ax.xaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    ax.yaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    plt.savefig(os.path.join(opts.save_val_results_path,'%d_overlay.png' % img_id), bbox_inches='tight', pad_inches=0)
                    plt.close()
                    img_id += 1

        score = metrics.get_results()
    return score, ret_samples

def infer(opts, model, loader, device, metrics, ret_samples_ids=None):
    """Do validation and return specified samples"""
    metrics.reset()
    ret_samples = []
    if opts.save_val_results:
        if not os.path.exists(opts.save_val_results_path):
            os.mkdir(opts.save_val_results_path)
        denorm = utils.Denormalize(mean=[0.485, 0.456, 0.406], 
                                   std=[0.229, 0.224, 0.225])
        img_id = 0
    
    with torch.no_grad():
        for i, images in tqdm(enumerate(loader)):
            
            images = images[0].to(device, dtype=torch.float32)#.unsqueeze(0)
            print(images.shape)
            #labels = labels.to(device, dtype=torch.long)
            
            outputs = model(images)
            preds = outputs.detach().max(dim=1)[1].cpu().numpy()
            #targets = labels.cpu().numpy()
            if ret_samples_ids is not None and i in ret_samples_ids:  # get vis samples
                ret_samples.append(
                            (images[0].detach().cpu().numpy(),  preds[0]))
            if opts.save_val_results:
                for i in range(len(images)):
                    image = images[i].detach().cpu().numpy()
                    pred = preds[i]
                    image = (denorm(image) * 255).transpose(1, 2, 0).astype(np.uint8)
                    print(pred.shape)
                    #pred[pred == 255] = 19  #loader.dataset.decode_target(pred).astype(np.uint8)
                    predfull = (pred==1) + (pred==1) + (pred == 9)  #merge road, sidewalk, terrain
                    print("\n\n\n\n\n\n")
                    print(type(predfull))
                    Image.fromarray(predfull).save(os.path.join(opts.save_val_results_path,'%d_predfull.png' % img_id))
                    Image.fromarray(image).save(os.path.join(opts.save_val_results_path,'%d_image.png' % img_id))
                    print(type(image))
                    
#                    for category in range(0, 21):
#                        predlocal = (pred==category)# + (pred==1) + (pred == 9)  #merge road, sidewalk, terrain
#                        Image.fromarray(predlocal).save(os.path.join(opts.save_val_results_path,f'%d_pred{category}.png' % img_id))
                    fig = plt.figure()
                    plt.imshow(image)
                    plt.axis('off')
                    plt.imshow(predfull, alpha=0.7)
                    ax = plt.gca()
                    ax.xaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    ax.yaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    plt.savefig(os.path.join(opts.save_val_results_path,'%d_overlay.png' % img_id), bbox_inches='tight', pad_inches=0)
                    plt.close()
                    img_id += 1
    return ret_samples


#def clusterize2(labels, categories):
#    
#    batch_size = labels.shape[0]
#    col_number = labels.shape[1]
#    row_number = labels.shape[2]
#    mod_labels = np.empty((batch_size, col_number, row_number))
#    
#    print("clusterizing: begin")
#    for batch in range(0, batch_size):
#        for cols in range(0, col_number):
#            for rows in range(0, row_number):
#                holder = labels[batch][cols][rows]
#                if holder > 19:
#                    holder = 19
#                mod_labels[batch][cols][rows] = categories[holder]
#    print('clusterizing: end')
#
#    return torch.tensor(mod_labels)
#    

#def clusterize(images):
#    print(type(images))
#    print("len:\t\t", len(images))
#    img_copy = images[0][1].copy()
#    print(img_copy)
#    print("0,0", img_copy[0,0])
#    img_copy[0,0]=0
#    print("0,0", img_copy[0,0])
#    print(type(images[0][1][0]))
#    print(type(images[0][1][0,0]))
#    
#    for _ in range(0,12):
#        print("a = ", img_copy)
#    
#    
#    cols = images[0][1].shape[0]
#    rows = images[0][1].shape[1]
##    print(type(images[0][1][0][0]));
##    print(images[0][1][0][0]);
#    line = [0]*cols;
#    arr = np.array(line)
#    img_copy1 = images[0][1].copy()
#    
#    if (img_copy==img_copy1).all():
#        print("diff");
#    else:
#        print("igual");
##    for a in range(0, cols):
##        print(a);
##        images[0][1][a][0]=sentinel
##        for b in range(0, rows):
##            images[0][1][a][b]=sentinel
#    print("\n\npre\n\n")
#    for _ in range(0,12):
#        print("a = ", images[0][1])
#    
#    print("len:\t\t", len(images))
#    holder0=images[0][1][0,0]
#    print(holder0)
#    print(images[0][1])
#    print("\n\npost\n\n")
#    holder1=images[0][1][0,0]
#    print(holder1)
#    print(images[0][1])
#    if holder0 != holder1:
#        print("\n\n\nfalse\n\n\n")
#    formated_img = Image.fromarray(images[0][1].astype(np.uint8)).save("test_image.png")#os.path.join(opts.save_val_results_path,'%d_image.png' % img_id))

def ptype(arg):
    print(type(arg))

def a():
    cols = pred.shape[0]
    rows = pred.shape[1]
    print(cols, rows)
    for a in range(0, cols):
        for b in range(0, rows):
            pred[a][b]=True


def main():
    opts = get_argparser().parse_args()
    if opts.dataset.lower() == 'voc':
        opts.num_classes = 21
    elif opts.dataset.lower() == 'cityscapes':
        opts.num_classes = 19 #TODO:read cat

    # Setup visualization
    vis = Visualizer(port=opts.vis_port,
                     env=opts.vis_env) if opts.enable_vis else None
    if vis is not None:  # display options
        vis.vis_table("Options", vars(opts))

    os.environ['CUDA_VISIBLE_DEVICES'] = opts.gpu_id
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print("Device: %s" % device)

    # Setup random seed
    torch.manual_seed(opts.random_seed)
    np.random.seed(opts.random_seed)
    random.seed(opts.random_seed)

    # Setup dataloader
    if opts.dataset=='voc' and not opts.crop_val:
        opts.val_batch_size = 1

    train_dst, val_dst, test_dst = get_dataset(opts)
    #clusterize(train_dst)

    print(train_dst)
    train_loader = data.DataLoader(
        train_dst, batch_size=opts.batch_size, shuffle=True, num_workers=2)
    print(train_loader)
    val_loader = data.DataLoader(
        val_dst, batch_size=opts.val_batch_size, shuffle=True, num_workers=2)
    test_loader = data.DataLoader(
        test_dst, batch_size=opts.val_batch_size, shuffle=False, num_workers=2)
    
    print("Dataset: %s, Train set: %d, Val set: %d Test set: %d" %
          (opts.dataset, len(train_dst), len(val_dst), len(test_dst)))


    # Set up model
    model_map = {
        'deeplabv3_resnet50': network.deeplabv3_resnet50,
        'deeplabv3plus_resnet50': network.deeplabv3plus_resnet50,
        'deeplabv3_resnet101': network.deeplabv3_resnet101,
        'deeplabv3plus_resnet101': network.deeplabv3plus_resnet101,
        'deeplabv3_mobilenet': network.deeplabv3_mobilenet,
        'deeplabv3plus_mobilenet': network.deeplabv3plus_mobilenet
    }

    model = model_map[opts.model](num_classes=opts.num_classes, output_stride=opts.output_stride)
    if opts.separable_conv and 'plus' in opts.model:
        network.convert_to_separable_conv(model.classifier)
    utils.set_bn_momentum(model.backbone, momentum=0.01)
    
    # Set up metrics
    metrics = StreamSegMetrics(opts.num_classes)

    # Set up optimizer
    optimizer = torch.optim.SGD(params=[
        {'params': model.backbone.parameters(), 'lr': 0.1*opts.lr},
        {'params': model.classifier.parameters(), 'lr': opts.lr},
    ], lr=opts.lr, momentum=0.9, weight_decay=opts.weight_decay)
    #optimizer = torch.optim.SGD(params=model.parameters(), lr=opts.lr, momentum=0.9, weight_decay=opts.weight_decay)
    #torch.optim.lr_scheduler.StepLR(optimizer, step_size=opts.lr_decay_step, gamma=opts.lr_decay_factor)
    if opts.lr_policy=='poly':
        scheduler = utils.PolyLR(optimizer, opts.total_itrs, power=0.9)
    elif opts.lr_policy=='step':
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=opts.step_size, gamma=0.1)

    # Set up criterion
    #criterion = utils.get_loss(opts.loss_type)
    if opts.loss_type == 'focal_loss':
        criterion = utils.FocalLoss(ignore_index=255, size_average=True)
    elif opts.loss_type == 'cross_entropy':
        criterion = nn.CrossEntropyLoss(ignore_index=255, reduction='mean')

    def save_ckpt(path):
        """ save current model
        """
        torch.save({
            "cur_itrs": cur_itrs,
            "model_state": model.module.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "scheduler_state": scheduler.state_dict(),
            "best_score": best_score,
        }, path)
        print("Model saved as %s" % path)
    
    utils.mkdir('checkpoints')
    # Restore
    best_score = 0.0
    cur_itrs = 0
    cur_epochs = 0
    if opts.ckpt is not None and os.path.isfile(opts.ckpt):
        # https://github.com/VainF/DeepLabV3Plus-Pytorch/issues/8#issuecomment-605601402, @PytaichukBohdan
        checkpoint = torch.load(opts.ckpt, map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint["model_state"])
        model = nn.DataParallel(model)
        model.to(device)
        if opts.continue_training:
            optimizer.load_state_dict(checkpoint["optimizer_state"])
            scheduler.load_state_dict(checkpoint["scheduler_state"])
            cur_itrs = checkpoint["cur_itrs"]
            best_score = checkpoint['best_score']
            print("Training state restored from %s" % opts.ckpt)
        print("Model restored from %s" % opts.ckpt)
        del checkpoint  # free memory
    else:
        print(opts.ckpt)
        print("[!] Retrain")
        model = nn.DataParallel(model)
        model.to(device)

    #==========   Train Loop   ==========#
    vis_sample_id = np.random.randint(0, len(val_loader), opts.vis_num_samples,
                                      np.int32) if opts.enable_vis else None  # sample idxs for visualization
    denorm = utils.Denormalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])  # denormalization for ori images

    if opts.test_only:
        model.eval()
        opts.val_batch_size = 1
        ret_samples = infer(opts=opts, model=model, loader=test_loader, device=device, metrics=metrics)

        return

    interval_loss = 0
#    number = 0
    while True: #cur_itrs < opts.total_itrs:
        # =====  Train  =====
        model.train()
        cur_epochs += 1
        for (images, labels) in train_loader:
#            number+=1
            cur_itrs += 1
#            debug_info(images)
#            print(images.shape)
#            debug_info(labels[0].float()/20)
#            #formated_img = Image.fromarray(labels[0]).save(f"test_image_{number}.png")
#            debug_info(images[0])
#            save_image(images[0].float()*20, f"labels/base_image_{number}.png")
#            save_image(labels[0].float()/20, f"labels/test_image_{number}.png")
            #labels = clusterize2(labels, cluster);
            
#            batch_size = labels.shape[0]
#            col_number = labels.shape[1]
#            row_number = labels.shape[2]
#            print(batch_size, col_number, row_number)
#            print("images: ", images.shape)
#            mod_labels = np.empty((opts.val_batch_size, col_number, row_number))
#            
#            print('clusterizing ctrl', )
#            for batch in range(0, batch_size):
#                for cols in range(0, col_number):
#                    for rows in range(0, row_number):
#                        holder = labels[batch][cols][rows]
#                        if holder > 19:
#                            holder = 19
#                        mod_labels[batch][cols][rows] = cluster[holder]
#            print('clusterizing ctrl')
#
#            save_image(tensor1[0]/20, f"labels/test_image_{number}_swap.png")
#
#            save_image(images[0][0].float(), f"labels/test_image_0_swap.png")
#            save_image(images[0][1].float(), f"labels/test_image_1_swap.png")
#            save_image(images[0][2].float(), f"labels/test_image_2_swap.png")
#            save_image(images[0].float(), f"labels/test_mix.png")
#            save_image(labels[0].float()/20, f"labels/test_label_2_swap.png")
            
            images = images.to(device, dtype=torch.float32)
            labels = labels.to(device, dtype=torch.long)
            #debug_info(images)

            optimizer.zero_grad()
            outputs = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            np_loss = loss.detach().cpu().numpy()
            interval_loss += np_loss
            if vis is not None:
                vis.vis_scalar('Loss', cur_itrs, np_loss)

            if (cur_itrs) % 10 == 0:
                interval_loss = interval_loss/10
                print("Epoch %d, Itrs %d/%d, Loss=%f" %
                      (cur_epochs, cur_itrs, opts.total_itrs, interval_loss))
                interval_loss = 0.0

            if (cur_itrs) % opts.val_interval == 0:
                save_ckpt('checkpoints/latest_%s_%s_os%d.pth' %
                          (opts.model, opts.dataset, opts.output_stride))
                print("validation...")
                model.eval()
                val_score, ret_samples = validate(
                    opts=opts, model=model, loader=val_loader, device=device, metrics=metrics, ret_samples_ids=vis_sample_id)
                print(metrics.to_str(val_score))
                if val_score['Mean IoU'] > best_score:  # save best model
                    best_score = val_score['Mean IoU']
                    save_ckpt('checkpoints/best_%s_%s_os%d.pth' %
                              (opts.model, opts.dataset,opts.output_stride))

                if vis is not None:  # visualize validation score and samples
                    vis.vis_scalar("[Val] Overall Acc", cur_itrs, val_score['Overall Acc'])
                    vis.vis_scalar("[Val] Mean IoU", cur_itrs, val_score['Mean IoU'])
                    vis.vis_table("[Val] Class IoU", val_score['Class IoU'])

                    for k, (img, target, lbl) in enumerate(ret_samples):
                        img = (denorm(img) * 255).astype(np.uint8)
                        target = train_dst.decode_target(target).transpose(2, 0, 1).astype(np.uint8)
                        lbl = train_dst.decode_target(lbl).transpose(2, 0, 1).astype(np.uint8)
                        concat_img = np.concatenate((img, target, lbl), axis=2)  # concat along width
                        vis.vis_image('Sample %d' % k, concat_img)
                model.train()
            scheduler.step()  

            if cur_itrs >=  opts.total_itrs:
                return

def debug_info(img):
    print(type(img))
    print("\n\n\n\n")
    print(img)
    print("\n\n\n\n")

if __name__ == '__main__':
    main()
