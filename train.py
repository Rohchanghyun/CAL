import time
import datetime
import logging
import torch
import wandb
from tqdm import tqdm
#from apex import amp
from tools.utils import AverageMeter



def train_cal(config, epoch, model, classifier, clothes_classifier, criterion_cla, criterion_pair, 
    criterion_clothes, criterion_adv, optimizer, optimizer_cc, trainloader, pid2clothes):
    logger = logging.getLogger('reid.train')
    batch_cla_loss = AverageMeter()
    batch_pair_loss = AverageMeter()
    batch_clo_loss = AverageMeter()
    batch_adv_loss = AverageMeter()
    corrects = AverageMeter()
    clothes_corrects = AverageMeter()
    batch_time = AverageMeter()
    data_time = AverageMeter()

    model.train()
    classifier.train()
    clothes_classifier.train()
    wandb.init(entity = "ggara376",project="CC-reid", name="CAL")
    end = time.time()
    
    current_device = torch.cuda.current_device()
    print(f"현재 학습 중인 GPU ID: {current_device}")

# 현재 사용 중인 GPU의 이름을 확인
    device_name = torch.cuda.get_device_name(current_device)
    print(f"현재 학습 중인 GPU 이름: {device_name}")
    pbar = tqdm(enumerate(trainloader), total=len(trainloader), desc=f"Epoch {epoch+1}")
    for batch_idx, (imgs, pids, camids, clothes_ids) in pbar:
        # Get all positive clothes classes (belonging to the same identity) for each sample
        pid2clothes = pid2clothes.cuda()
        pos_mask = pid2clothes[pids]
        imgs, pids, clothes_ids, pos_mask = imgs.cuda(), pids.cuda(), clothes_ids.cuda(), pos_mask.float().cuda()
        # Measure data loading time
        data_time.update(time.time() - end)
        # Forward
        features = model(imgs)
        outputs = classifier(features)
        pred_clothes = clothes_classifier(features.detach())
        _, preds = torch.max(outputs.data, 1)

        # Update the clothes discriminator
        clothes_loss = criterion_clothes(pred_clothes, clothes_ids)
        if epoch >= config.TRAIN.START_EPOCH_CC:
            optimizer_cc.zero_grad()
            if config.TRAIN.AMP:
                with amp.scale_loss(clothes_loss, optimizer_cc) as scaled_loss:
                    scaled_loss.backward()
            else:
                clothes_loss.backward()
            optimizer_cc.step()

        # Update the backbone
        new_pred_clothes = clothes_classifier(features)
        _, clothes_preds = torch.max(new_pred_clothes.data, 1)

        # Compute loss
        cla_loss = criterion_cla(outputs, pids)
        pair_loss = criterion_pair(features, pids)
        adv_loss = criterion_adv(new_pred_clothes, clothes_ids, pos_mask)
        if epoch >= config.TRAIN.START_EPOCH_ADV:
            loss = cla_loss + adv_loss + config.LOSS.PAIR_LOSS_WEIGHT * pair_loss   
        else:
            loss = cla_loss + config.LOSS.PAIR_LOSS_WEIGHT * pair_loss   
        optimizer.zero_grad()
        if config.TRAIN.AMP:
            with amp.scale_loss(loss, optimizer) as scaled_loss:
                scaled_loss.backward()
        else:
            loss.backward()
        optimizer.step()

        # statistics
        corrects.update(torch.sum(preds == pids.data).float()/pids.size(0), pids.size(0))
        clothes_corrects.update(torch.sum(clothes_preds == clothes_ids.data).float()/clothes_ids.size(0), clothes_ids.size(0))
        batch_cla_loss.update(cla_loss.item(), pids.size(0))
        batch_pair_loss.update(pair_loss.item(), pids.size(0))
        batch_clo_loss.update(clothes_loss.item(), clothes_ids.size(0))
        batch_adv_loss.update(adv_loss.item(), clothes_ids.size(0))
        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()
                # Wandb 로깅
        wandb.log({
            "epoch": epoch + 1,
            "batch": batch_idx,
            "classification_loss": cla_loss.item(),
            "pair_loss": pair_loss.item(),
            "clothes_loss": clothes_loss.item(),
            "adversarial_loss": adv_loss.item(),
            "accuracy": corrects.avg,
            "clothes_accuracy": clothes_corrects.avg,
        })

        # tqdm 프로그레스 바 업데이트
        pbar.set_postfix({
            'ClaLoss': f"{batch_cla_loss.avg:.4f}",
            'PairLoss': f"{batch_pair_loss.avg:.4f}",
            'CloLoss': f"{batch_clo_loss.avg:.4f}",
            'AdvLoss': f"{batch_adv_loss.avg:.4f}",
            'Acc': f"{corrects.avg:.2%}",
            'CloAcc': f"{clothes_corrects.avg:.2%}"
        })

    wandb.log({
        "epoch": epoch + 1,
        "epoch_classification_loss": batch_cla_loss.avg,
        "epoch_pair_loss": batch_pair_loss.avg,
        "epoch_clothes_loss": batch_clo_loss.avg,
        "epoch_adversarial_loss": batch_adv_loss.avg,
        "epoch_accuracy": corrects.avg,
        "epoch_clothes_accuracy": clothes_corrects.avg,
    })
    
    logger.info('Epoch{0} '
                  'Time:{batch_time.sum:.1f}s '
                  'Data:{data_time.sum:.1f}s '
                  'ClaLoss:{cla_loss.avg:.4f} '
                  'PairLoss:{pair_loss.avg:.4f} '
                  'CloLoss:{clo_loss.avg:.4f} '
                  'AdvLoss:{adv_loss.avg:.4f} '
                  'Acc:{acc.avg:.2%} '
                  'CloAcc:{clo_acc.avg:.2%} '.format(
                   epoch+1, batch_time=batch_time, data_time=data_time, 
                   cla_loss=batch_cla_loss, pair_loss=batch_pair_loss, 
                   clo_loss=batch_clo_loss, adv_loss=batch_adv_loss, 
                   acc=corrects, clo_acc=clothes_corrects))


def train_cal_with_memory(config, epoch, model, classifier, criterion_cla, criterion_pair, 
    criterion_adv, optimizer, trainloader, pid2clothes):
    logger = logging.getLogger('reid.train')
    batch_cla_loss = AverageMeter()
    batch_pair_loss = AverageMeter()
    batch_adv_loss = AverageMeter()
    corrects = AverageMeter()
    batch_time = AverageMeter()
    data_time = AverageMeter()

    model.train()
    classifier.train()

    end = time.time()
    for batch_idx, (imgs, pids, camids, clothes_ids) in enumerate(trainloader):
        # Get all positive clothes classes (belonging to the same identity) for each sample
        pos_mask = pid2clothes[pids]
        imgs, pids, clothes_ids, pos_mask = imgs.cuda(), pids.cuda(), clothes_ids.cuda(), pos_mask.float().cuda()
        # Measure data loading time
        data_time.update(time.time() - end)
        # Forward
        features = model(imgs)
        outputs = classifier(features)
        _, preds = torch.max(outputs.data, 1)

        # Compute loss
        cla_loss = criterion_cla(outputs, pids)
        pair_loss = criterion_pair(features, pids)

        if epoch >= config.TRAIN.START_EPOCH_ADV:
            adv_loss = criterion_adv(features, clothes_ids, pos_mask)
            loss = cla_loss + adv_loss + config.LOSS.PAIR_LOSS_WEIGHT * pair_loss   
        else:
            loss = cla_loss + config.LOSS.PAIR_LOSS_WEIGHT * pair_loss  

        optimizer.zero_grad()
        if config.TRAIN.AMP:
            with amp.scale_loss(loss, optimizer) as scaled_loss:
                scaled_loss.backward()
        else:
            loss.backward()
        optimizer.step()

        # statistics
        corrects.update(torch.sum(preds == pids.data).float()/pids.size(0), pids.size(0))
        batch_cla_loss.update(cla_loss.item(), pids.size(0))
        batch_pair_loss.update(pair_loss.item(), pids.size(0))
        if epoch >= config.TRAIN.START_EPOCH_ADV: 
            batch_adv_loss.update(adv_loss.item(), clothes_ids.size(0))
        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

    logger.info('Epoch{0} '
                'Time:{batch_time.sum:.1f}s '
                'Data:{data_time.sum:.1f}s '
                'ClaLoss:{cla_loss.avg:.4f} '
                'PairLoss:{pair_loss.avg:.4f} '
                'AdvLoss:{adv_loss.avg:.4f} '
                'Acc:{acc.avg:.2%} '.format(
                epoch+1, batch_time=batch_time, data_time=data_time, 
                cla_loss=batch_cla_loss, pair_loss=batch_pair_loss, 
                adv_loss=batch_adv_loss, acc=corrects))
    
    
    
def train_base(config, epoch, model, classifier, clothes_classifier, criterion_cla, criterion_pair, 
    criterion_clothes, criterion_adv, optimizer, optimizer_cc, trainloader, pid2clothes):
    logger = logging.getLogger('reid.train')
    batch_cla_loss = AverageMeter()
    batch_pair_loss = AverageMeter()
    batch_clo_loss = AverageMeter()
    batch_adv_loss = AverageMeter()
    corrects = AverageMeter()
    clothes_corrects = AverageMeter()
    batch_time = AverageMeter()
    data_time = AverageMeter()

    model.train()
    classifier.train()
    clothes_classifier.train()
    wandb.init(entity = "ggara376",project="CC-reid", name="CAL")
    end = time.time()
    
    current_device = torch.cuda.current_device()
    print(f"현재 학습 중인 GPU ID: {current_device}")

# 현재 사용 중인 GPU의 이름을 확인
    device_name = torch.cuda.get_device_name(current_device)
    print(f"현재 학습 중인 GPU 이름: {device_name}")
    pbar = tqdm(enumerate(trainloader), total=len(trainloader), desc=f"Epoch {epoch+1}")
    for batch_idx, (imgs, pids, camids, clothes_ids) in pbar:
        # Get all positive clothes classes (belonging to the same identity) for each sample
        pid2clothes = pid2clothes.cuda()
        pos_mask = pid2clothes[pids]
        imgs, pids, clothes_ids, pos_mask = imgs.cuda(), pids.cuda(), clothes_ids.cuda(), pos_mask.float().cuda()
        # Measure data loading time
        data_time.update(time.time() - end)
        # Forward
        features = model(imgs)
        outputs = classifier(features)
        pred_clothes = clothes_classifier(features.detach())
        _, preds = torch.max(outputs.data, 1)


        # Compute loss
        cla_loss = criterion_cla(outputs, pids)
        pair_loss = criterion_pair(features, pids)
        loss = cla_loss + config.LOSS.PAIR_LOSS_WEIGHT * pair_loss   
        optimizer.zero_grad()
        if config.TRAIN.AMP:
            with amp.scale_loss(loss, optimizer) as scaled_loss:
                scaled_loss.backward()
        else:
            loss.backward()
        optimizer.step()

        # statistics
        corrects.update(torch.sum(preds == pids.data).float()/pids.size(0), pids.size(0))
        batch_cla_loss.update(cla_loss.item(), pids.size(0))
        batch_pair_loss.update(pair_loss.item(), pids.size(0))
        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()
                # Wandb 로깅
        wandb.log({
            "epoch": epoch + 1,
            "batch": batch_idx,
            "classification_loss": cla_loss.item(),
            "pair_loss": pair_loss.item(),
            "accuracy": corrects.avg,
        })

        # tqdm 프로그레스 바 업데이트
        pbar.set_postfix({
            'ClaLoss': f"{batch_cla_loss.avg:.4f}",
            'PairLoss': f"{batch_pair_loss.avg:.4f}",
            'Acc': f"{corrects.avg:.2%}"
        })

    wandb.log({
        "epoch": epoch + 1,
        "epoch_classification_loss": batch_cla_loss.avg,
        "epoch_pair_loss": batch_pair_loss.avg,
        "epoch_accuracy": corrects.avg
    })
    
    logger.info('Epoch{0} '
                  'Time:{batch_time.sum:.1f}s '
                  'Data:{data_time.sum:.1f}s '
                  'ClaLoss:{cla_loss.avg:.4f} '
                  'PairLoss:{pair_loss.avg:.4f} '
                  'Acc:{acc.avg:.2%} '.format(
                   epoch+1, batch_time=batch_time, data_time=data_time, 
                   cla_loss=batch_cla_loss, pair_loss=batch_pair_loss,  
                   acc=corrects))